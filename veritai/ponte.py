"""Ponte entre o FOMO e a VeritAI no SageMaker (endpoint assíncrono).

Expõe o mesmo POST /analisar do serviço: grava o pedido no S3, chama o endpoint assíncrono e espera o
relatório na pasta de saída. O FOMO não muda (só o VERITAI_URL), e só a ponte tem credencial da AWS.

    uvicorn veritai.ponte:criar_app --factory --host 0.0.0.0 --port 8100

Variáveis: VERITAI_SAGEMAKER_ENDPOINT, VERITAI_S3_BUCKET, VERITAI_S3_PREFIXO (padrão "veritai"),
VERITAI_PONTE_ESPERA (segundos, padrão 1200) e a região da AWS (AWS_REGION).
"""

import asyncio
import os
import time
import uuid
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from .relatorio import PedidoAnalise, RelatorioAnalise


@dataclass(frozen=True)
class Destino:
    endpoint: str
    bucket: str
    prefixo: str = "veritai"
    # Com o endpoint em zero instâncias, ligar a máquina e carregar o container leva alguns minutos.
    espera: float = 1200
    intervalo: float = 5


class PonteErro(Exception):
    """Falha do SageMaker ou do S3; a mensagem não traz detalhes internos."""


def destino_do_ambiente() -> Destino:
    return Destino(
        endpoint=os.environ["VERITAI_SAGEMAKER_ENDPOINT"],
        bucket=os.environ["VERITAI_S3_BUCKET"],
        prefixo=os.getenv("VERITAI_S3_PREFIXO", "veritai").strip("/"),
        espera=float(os.getenv("VERITAI_PONTE_ESPERA", "1200")),
    )


def separar_s3(url: str) -> tuple[str, str]:
    if not url.startswith("s3://"):
        raise PonteErro("O SageMaker devolveu um local de saída inválido.")
    bucket, _, chave = url[len("s3://"):].partition("/")
    return bucket, chave


def ler_se_existir(s3, url: str) -> bytes | None:
    # Confere pela listagem do próprio prefixo: assim o papel IAM só precisa listar veritai/*, e um objeto
    # que ainda não chegou não se confunde com falta de permissão (403).
    bucket, chave = separar_s3(url)
    listagem = s3.list_objects_v2(Bucket=bucket, Prefix=chave, MaxKeys=1)
    if not any(objeto["Key"] == chave for objeto in listagem.get("Contents", [])):
        return None
    return s3.get_object(Bucket=bucket, Key=chave)["Body"].read()


def apagar(s3, *urls: str) -> None:
    # O pedido traz o texto da notícia: não fica guardado depois da análise (o bucket também expira em 1 dia).
    for url in urls:
        try:
            s3.delete_object(Bucket=separar_s3(url)[0], Key=separar_s3(url)[1])
        except Exception:
            pass


def analisar_no_sagemaker(pedido: PedidoAnalise, destino: Destino, s3, runtime, dormir=time.sleep, relogio=time.monotonic) -> RelatorioAnalise:
    entrada = f"s3://{destino.bucket}/{destino.prefixo}/entradas/{uuid.uuid4().hex}.json"
    try:
        s3.put_object(Bucket=destino.bucket, Key=separar_s3(entrada)[1], Body=pedido.model_dump_json().encode("utf-8"), ContentType="application/json")
        resposta = runtime.invoke_endpoint_async(
            EndpointName=destino.endpoint,
            InputLocation=entrada,
            ContentType="application/json",
            Accept="application/json",
            InvocationTimeoutSeconds=900,
            # Se a ponte desistir, o pedido também expira na fila do SageMaker.
            RequestTTLSeconds=max(60, int(destino.espera)),
        )
    except Exception as exc:
        apagar(s3, entrada)
        raise PonteErro(f"Não foi possível enviar o pedido ao SageMaker ({type(exc).__name__}).") from exc
    saida, falha = resposta["OutputLocation"], resposta.get("FailureLocation")

    inicio = relogio()
    try:
        while True:
            try:
                corpo = ler_se_existir(s3, saida)
                falhou = corpo is None and falha is not None and ler_se_existir(s3, falha) is not None
            except PonteErro:
                raise
            except Exception as exc:
                raise PonteErro(f"Não foi possível ler o resultado no S3 ({type(exc).__name__}).") from exc
            if corpo is not None:
                try:
                    return RelatorioAnalise.model_validate_json(corpo)
                except ValidationError as exc:
                    raise PonteErro("A resposta do SageMaker não segue o contrato da VeritAI.") from exc
            if falhou:
                raise PonteErro("A VeritAI no SageMaker falhou ao analisar o pedido (detalhes no CloudWatch).")
            if relogio() - inicio >= destino.espera:
                raise TimeoutError
            dormir(destino.intervalo)
    finally:
        apagar(s3, *(url for url in (entrada, saida, falha) if url))


def criar_app(destino: Destino | None = None, s3=None, runtime=None) -> FastAPI:
    app = FastAPI(title="VeritAI (ponte SageMaker)", version="0.1.0", description="Repassa POST /analisar ao endpoint assíncrono da VeritAI no SageMaker.")
    destino = destino or destino_do_ambiente()
    if s3 is None or runtime is None:
        import boto3

        s3 = s3 or boto3.client("s3")
        runtime = runtime or boto3.client("sagemaker-runtime")

    @app.get("/health")
    def health():
        return {"status": "ok", "modo": "sagemaker-assincrono", "endpoint": destino.endpoint}

    @app.post("/analisar", response_model=RelatorioAnalise)
    async def analisar_noticia(pedido: PedidoAnalise):
        try:
            return await asyncio.to_thread(analisar_no_sagemaker, pedido, destino, s3, runtime)
        except TimeoutError:
            raise HTTPException(504, f"A VeritAI no SageMaker não respondeu em {destino.espera:.0f} s.") from None
        except PonteErro as exc:
            raise HTTPException(502, str(exc)) from None

    return app
