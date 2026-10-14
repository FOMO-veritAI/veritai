"""Deploy da VeritAI e do FOMO na AWS (CloudFormation + ECR). Rode da raiz do repositório veritai:

    python deploy/aws/implantar.py base --email voce@exemplo.com [--orcamento 20]
    python deploy/aws/implantar.py imagem      # constrói veritai:sagemaker e envia ao ECR (tag = commit atual)
    python deploy/aws/implantar.py app         # SageMaker assíncrono + EC2 com k3s + CloudFront
    python deploy/aws/implantar.py status
    python deploy/aws/implantar.py pausar      # desliga a EC2 (o SageMaker já vai a zero sozinho)
    python deploy/aws/implantar.py retomar
    python deploy/aws/implantar.py chaves      # mostra as chaves do publisher e do revisor (só no seu terminal)
    python deploy/aws/implantar.py destruir --sim

Região: --regiao (padrão: AWS_REGION ou us-east-1). Credenciais: as do AWS CLI (aws configure ou aws sso login).
Precisa do extra aws: pip install -e ".[aws]".
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, WaiterError

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parents[1]
PILHA_BASE, PILHA_APP = "veritai-base", "veritai-app"
ENDPOINT = "veritai-v0"
IMAGEM_LOCAL = "veritai:sagemaker"
ESPERA = {"Delay": 15, "MaxAttempts": 240}  # até 1 h: endpoint e CloudFront demoram


def saidas(cf, pilha: str) -> dict[str, str]:
    try:
        resposta = cf.describe_stacks(StackName=pilha)["Stacks"][0]
    except ClientError:
        sys.exit(f"A pilha {pilha} não existe. Rode antes o passo que a cria.")
    return {item["OutputKey"]: item["OutputValue"] for item in resposta.get("Outputs", [])}


def estado(cf, pilha: str) -> str | None:
    try:
        return cf.describe_stacks(StackName=pilha)["Stacks"][0]["StackStatus"]
    except ClientError:
        return None


def mostrar_falhas(cf, pilha: str) -> None:
    for evento in cf.describe_stack_events(StackName=pilha)["StackEvents"][:40]:
        if evento["ResourceStatus"].endswith("FAILED"):
            print(f"  {evento['LogicalResourceId']}: {evento.get('ResourceStatusReason', '')}")


def implantar_pilha(cf, pilha: str, modelo: str, parametros: dict) -> dict[str, str]:
    argumentos = dict(
        StackName=pilha,
        TemplateBody=(AQUI / modelo).read_text(encoding="utf-8"),
        Parameters=[{"ParameterKey": k, "ParameterValue": str(v)} for k, v in parametros.items()],
        Capabilities=["CAPABILITY_IAM"],
    )
    atual = estado(cf, pilha)
    if atual == "ROLLBACK_COMPLETE":
        print(f"{pilha} falhou na criação anterior; apagando para criar de novo...")
        cf.delete_stack(StackName=pilha)
        cf.get_waiter("stack_delete_complete").wait(StackName=pilha, WaiterConfig=ESPERA)
        atual = None
    try:
        if atual is None:
            print(f"Criando {pilha} (pode levar de 10 a 30 min)...")
            cf.create_stack(**argumentos)
            cf.get_waiter("stack_create_complete").wait(StackName=pilha, WaiterConfig=ESPERA)
        else:
            print(f"Atualizando {pilha}...")
            try:
                cf.update_stack(**argumentos)
            except ClientError as exc:
                if "No updates are to be performed" not in str(exc):
                    raise
                print("Nada mudou.")
                return saidas(cf, pilha)
            cf.get_waiter("stack_update_complete").wait(StackName=pilha, WaiterConfig=ESPERA)
    except WaiterError:
        print(f"{pilha} não terminou bem ({estado(cf, pilha)}). Recursos com falha:")
        mostrar_falhas(cf, pilha)
        sys.exit(1)
    print(f"{pilha}: ok")
    return saidas(cf, pilha)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=RAIZ, check=True, capture_output=True, text=True).stdout.strip()


def cmd_base(sessao, args):
    resultado = implantar_pilha(sessao.client("cloudformation"), PILHA_BASE, "base.yaml", {"EmailAlerta": args.email, "OrcamentoMensalUSD": args.orcamento})
    print(json.dumps(resultado, indent=2))
    print("Confirme a inscrição no e-mail de alerta de orçamento, se a AWS pedir.")


def cmd_imagem(sessao, args):
    repositorio = saidas(sessao.client("cloudformation"), PILHA_BASE)["Repositorio"]
    if git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("Há mudanças não commitadas: a tag da imagem precisa corresponder a um commit.")
    tag = git("rev-parse", "--short", "HEAD")
    if not args.sem_build:
        subprocess.run(["docker", "build", "--platform", "linux/amd64", "--build-arg", "PERFIL_EMBUTIDO=v0", "-t", IMAGEM_LOCAL, "."], cwd=RAIZ, check=True)
    dados = sessao.client("ecr").get_authorization_token()["authorizationData"][0]
    usuario, senha = base64.b64decode(dados["authorizationToken"]).decode().split(":", 1)
    subprocess.run(["docker", "login", "--username", usuario, "--password-stdin", dados["proxyEndpoint"]], input=senha, text=True, check=True)
    subprocess.run(["docker", "tag", IMAGEM_LOCAL, f"{repositorio}:{tag}"], check=True)
    subprocess.run(["docker", "push", f"{repositorio}:{tag}"], check=True)
    print(f"Imagem enviada: {repositorio}:{tag}")


def ultima_tag(sessao) -> str:
    imagens = sessao.client("ecr").describe_images(repositoryName="veritai", filter={"tagStatus": "TAGGED"})["imageDetails"]
    if not imagens:
        sys.exit("Nenhuma imagem no ECR. Rode antes: implantar.py imagem")
    return max(imagens, key=lambda i: i["imagePushedAt"])["imageTags"][0]


def cmd_app(sessao, args):
    cf = sessao.client("cloudformation")
    bucket = saidas(cf, PILHA_BASE)["Bucket"]
    listas = sessao.client("ec2").describe_managed_prefix_lists(
        Filters=[{"Name": "prefix-list-name", "Values": ["com.amazonaws.global.cloudfront.origin-facing"]}]
    )["PrefixLists"]
    tag = args.tag or ultima_tag(sessao)
    resultado = implantar_pilha(cf, PILHA_APP, "app.yaml", {
        "Bucket": bucket,
        "TagImagem": tag,
        "PrefixoCloudFront": listas[0]["PrefixListId"],
        "RefManifests": args.ref,
        "TipoInstanciaSageMaker": args.tipo_sagemaker,
        "TipoInstanciaEC2": args.tipo_ec2,
    })
    print(json.dumps(resultado, indent=2))
    print(f"\nAPI do FOMO: {resultado['UrlApi']} (o k3s leva uns 3 min para subir depois da pilha)")
    print("Configure FOMO_API_URL no GitHub com esse endereço e rode de novo o workflow do Pages do FOMO.")


def cmd_status(sessao, args):
    cf = sessao.client("cloudformation")
    for pilha in (PILHA_BASE, PILHA_APP):
        print(f"{pilha}: {estado(cf, pilha) or 'não existe'}")
    if estado(cf, PILHA_APP) is None:
        return
    resultado = saidas(cf, PILHA_APP)
    ec2 = sessao.client("ec2").describe_instances(InstanceIds=[resultado["Instancia"]])["Reservations"][0]["Instances"][0]
    print(f"EC2 {resultado['Instancia']}: {ec2['State']['Name']}")
    endpoint = sessao.client("sagemaker").describe_endpoint(EndpointName=ENDPOINT)
    variante = endpoint["ProductionVariants"][0]
    print(f"SageMaker {ENDPOINT}: {endpoint['EndpointStatus']}, {variante.get('CurrentInstanceCount', 0)} instância(s) ligada(s)")
    try:
        with urllib.request.urlopen(f"{resultado['UrlApi']}/health", timeout=15) as resposta:
            print(f"API {resultado['UrlApi']}/health: HTTP {resposta.status}")
    except Exception as exc:
        print(f"API {resultado['UrlApi']}/health: sem resposta ({type(exc).__name__})")


def instancia(sessao) -> str:
    return saidas(sessao.client("cloudformation"), PILHA_APP)["Instancia"]


def cmd_pausar(sessao, args):
    sessao.client("ec2").stop_instances(InstanceIds=[instancia(sessao)])
    print("EC2 desligando. O FOMO online fica fora do ar até 'retomar'. O SageMaker vai a zero sozinho.")


def cmd_retomar(sessao, args):
    sessao.client("ec2").start_instances(InstanceIds=[instancia(sessao)])
    print("EC2 ligando; a API volta em uns 2 min.")


def cmd_chaves(sessao, args):
    resultado = saidas(sessao.client("cloudformation"), PILHA_APP)
    segredos = sessao.client("secretsmanager")
    for nome in ("ChavePublisher", "ChaveRevisor"):
        print(f"{nome}: {segredos.get_secret_value(SecretId=resultado[nome])['SecretString']}")


def cmd_destruir(sessao, args):
    if not args.sim:
        sys.exit("Isto apaga tudo na AWS (pilhas, imagens, bucket e o banco do FOMO na EC2). Confirme com --sim.")
    cf = sessao.client("cloudformation")
    if estado(cf, PILHA_APP):
        print(f"Apagando {PILHA_APP}...")
        cf.delete_stack(StackName=PILHA_APP)
        cf.get_waiter("stack_delete_complete").wait(StackName=PILHA_APP, WaiterConfig=ESPERA)
    if estado(cf, PILHA_BASE):
        bucket = saidas(cf, PILHA_BASE).get("Bucket")
        if bucket:
            sessao.resource("s3").Bucket(bucket).objects.all().delete()
        print(f"Apagando {PILHA_BASE}...")
        cf.delete_stack(StackName=PILHA_BASE)
        cf.get_waiter("stack_delete_complete").wait(StackName=PILHA_BASE, WaiterConfig=ESPERA)
    print("Tudo apagado.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--regiao", default=os.getenv("AWS_REGION") or "us-east-1")
    sub = parser.add_subparsers(dest="comando", required=True)
    base = sub.add_parser("base")
    base.add_argument("--email", required=True)
    base.add_argument("--orcamento", type=int, default=20)
    imagem = sub.add_parser("imagem")
    imagem.add_argument("--sem-build", action="store_true", help="envia a imagem veritai:sagemaker já construída")
    app = sub.add_parser("app")
    app.add_argument("--tag", help="tag da imagem no ECR (padrão: a mais recente)")
    app.add_argument("--ref", default="main", help="branch ou commit com deploy/k8s/fomo.yaml")
    app.add_argument("--tipo-sagemaker", default="ml.m5.large")
    app.add_argument("--tipo-ec2", default="t3.medium")
    for nome in ("status", "pausar", "retomar", "chaves"):
        sub.add_parser(nome)
    destruir = sub.add_parser("destruir")
    destruir.add_argument("--sim", action="store_true")
    args = parser.parse_args(argv)
    sessao = boto3.Session(region_name=args.regiao)
    globals()[f"cmd_{args.comando}"](sessao, args)


if __name__ == "__main__":
    main()
