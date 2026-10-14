"""Ponte para o SageMaker e rotas do SageMaker na API, com S3 e SageMaker simulados."""

import io
import json

import pytest
from fastapi.testclient import TestClient

from veritai.api import criar_app as criar_app_servico
from veritai.ponte import Destino, PonteErro, analisar_no_sagemaker, criar_app
from veritai.relatorio import PedidoAnalise

from test_veritai import ComparadorFixo, buscador_com, fonte, pedido


class S3Falso:
    """Como o papel IAM da EC2: só lista dentro de veritai/; ler objeto inexistente é erro (403 na AWS)."""

    def __init__(self):
        self.objetos: dict[tuple[str, str], bytes] = {}
        self.apagados: list[str] = []

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objetos[Bucket, Key] = Body

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        assert Prefix.startswith("veritai/"), "listagem fora do prefixo permitido"
        chaves = sorted(chave for balde, chave in self.objetos if balde == Bucket and chave.startswith(Prefix))
        return {"Contents": [{"Key": chave} for chave in chaves[:MaxKeys]]} if chaves else {}

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objetos:
            raise PermissionError("AccessDenied")
        return {"Body": io.BytesIO(self.objetos[Bucket, Key])}

    def delete_object(self, Bucket, Key):
        self.objetos.pop((Bucket, Key), None)
        self.apagados.append(Key)


class RuntimeFalso:
    """Simula o endpoint: depois de `atraso` consultas ao S3, grava a saída (ou a falha)."""

    def __init__(self, s3: S3Falso, servico: TestClient | None = None, falhar: bool = False):
        self.s3, self.servico, self.falhar = s3, servico, falhar
        self.chamadas: list[dict] = []

    def invoke_endpoint_async(self, **kwargs):
        self.chamadas.append(kwargs)
        bucket, chave = kwargs["InputLocation"][5:].split("/", 1)
        if self.falhar:
            self.s3.objetos[bucket, "veritai/falhas/x.out"] = b"erro"
        else:
            corpo = json.loads(self.s3.objetos[bucket, chave])
            relatorio = self.servico.post("/invocations", json=corpo)
            assert relatorio.status_code == 200, relatorio.text
            self.s3.objetos[bucket, "veritai/saidas/x.out"] = relatorio.content
        return {"OutputLocation": f"s3://{bucket}/veritai/saidas/x.out", "FailureLocation": f"s3://{bucket}/veritai/falhas/x.out"}


DESTINO = Destino(endpoint="veritai-v0", bucket="balde", espera=30, intervalo=1)


def servico():
    comparador = ComparadorFixo({"a.gov.br": ("entailment", 0.8, 0.9)})
    return TestClient(criar_app_servico(comparador, buscador_com(fonte("a.gov.br"))))


def test_rotas_do_sagemaker_respondem_como_health_e_analisar():
    client = servico()
    assert client.get("/ping").json() == client.get("/health").json()
    assert client.post("/invocations", json=pedido()).json() == client.post("/analisar", json=pedido()).json()
    assert client.post("/invocations", json=pedido(afirmacoes=[])).status_code == 422
    caminhos = client.get("/openapi.json").json()["paths"]
    assert "/ping" not in caminhos and "/invocations" not in caminhos


def test_ponte_envia_ao_sagemaker_e_devolve_o_relatorio():
    s3 = S3Falso()
    runtime = RuntimeFalso(s3, servico())
    client = TestClient(criar_app(DESTINO, s3, runtime))
    resposta = client.post("/analisar", json=pedido())
    assert resposta.status_code == 200, resposta.text
    assert resposta.json() == servico().post("/analisar", json=pedido()).json()
    chamada = runtime.chamadas[0]
    assert chamada["EndpointName"] == "veritai-v0"
    assert chamada["InputLocation"].startswith("s3://balde/veritai/entradas/")
    assert chamada["ContentType"] == "application/json"
    # Pedido e relatório não ficam no bucket depois da análise.
    assert s3.objetos == {}


def test_ponte_valida_o_pedido_antes_de_chamar_a_aws():
    s3 = S3Falso()
    runtime = RuntimeFalso(s3, servico())
    client = TestClient(criar_app(DESTINO, s3, runtime))
    assert client.post("/analisar", json=pedido(afirmacoes=["curta"])).status_code == 422
    assert runtime.chamadas == [] and s3.objetos == {}


def test_falha_no_sagemaker_vira_502_sem_detalhes():
    s3 = S3Falso()
    client = TestClient(criar_app(DESTINO, s3, RuntimeFalso(s3, falhar=True)))
    resposta = client.post("/analisar", json=pedido())
    assert resposta.status_code == 502
    assert "CloudWatch" in resposta.json()["detail"]
    assert s3.objetos == {}


def test_saida_fora_do_contrato_vira_erro():
    s3 = S3Falso()

    class RuntimeQuebrado(RuntimeFalso):
        def invoke_endpoint_async(self, **kwargs):
            self.s3.objetos["balde", "veritai/saidas/x.out"] = b'{"afirmacoes": "nada"}'
            return {"OutputLocation": "s3://balde/veritai/saidas/x.out"}

    with pytest.raises(PonteErro, match="contrato"):
        analisar_no_sagemaker(PedidoAnalise.model_validate(pedido()), DESTINO, s3, RuntimeQuebrado(s3), dormir=lambda _: None)


def test_sem_resposta_no_prazo_vira_504():
    s3 = S3Falso()

    class RuntimeParado(RuntimeFalso):
        def invoke_endpoint_async(self, **kwargs):
            return {"OutputLocation": "s3://balde/veritai/saidas/x.out"}

    tempo = iter(range(0, 1000, 10))
    with pytest.raises(TimeoutError):
        analisar_no_sagemaker(PedidoAnalise.model_validate(pedido()), DESTINO, s3, RuntimeParado(s3), dormir=lambda _: None, relogio=lambda: next(tempo))
    assert s3.objetos == {}


def test_erro_ao_chamar_o_sagemaker_vira_ponte_erro():
    s3 = S3Falso()

    class RuntimeSemCredencial(RuntimeFalso):
        def invoke_endpoint_async(self, **kwargs):
            raise RuntimeError("NoCredentialsError: segredo")

    client = TestClient(criar_app(DESTINO, s3, RuntimeSemCredencial(s3)))
    resposta = client.post("/analisar", json=pedido())
    assert resposta.status_code == 502
    assert "segredo" not in resposta.text
    assert s3.objetos == {}


def test_erro_ao_ler_o_resultado_vira_502_sem_detalhes():
    class S3Quebrado(S3Falso):
        def list_objects_v2(self, **kwargs):
            raise RuntimeError("AccessDenied: arn:aws:iam::123:user/x")

    s3 = S3Quebrado()

    class RuntimeOk(RuntimeFalso):
        def invoke_endpoint_async(self, **kwargs):
            return {"OutputLocation": "s3://balde/veritai/saidas/x.out"}

    resposta = TestClient(criar_app(DESTINO, s3, RuntimeOk(s3))).post("/analisar", json=pedido())
    assert resposta.status_code == 502
    assert "arn:aws" not in resposta.text and "S3" in resposta.json()["detail"]


def test_health_da_ponte_e_destino_do_ambiente(monkeypatch):
    from veritai.ponte import destino_do_ambiente

    monkeypatch.setenv("VERITAI_SAGEMAKER_ENDPOINT", "veritai-v0")
    monkeypatch.setenv("VERITAI_S3_BUCKET", "balde")
    monkeypatch.setenv("VERITAI_S3_PREFIXO", "/veritai/")
    destino = destino_do_ambiente()
    assert (destino.endpoint, destino.bucket, destino.prefixo, destino.espera) == ("veritai-v0", "balde", "veritai", 1200)
    corpo = TestClient(criar_app(destino, S3Falso(), RuntimeFalso(S3Falso()))).get("/health").json()
    assert corpo == {"status": "ok", "modo": "sagemaker-assincrono", "endpoint": "veritai-v0"}
