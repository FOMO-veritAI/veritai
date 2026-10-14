# Deploy na AWS: SageMaker + Kubernetes (k3s)

```
GitHub Pages (frontend do FOMO)
        │ HTTPS
        ▼
CloudFront  https://xxxx.cloudfront.net
        │ HTTP, só de IPs do CloudFront
        ▼
EC2 t3.medium com k3s ── namespace fomo
        ├─ fomo-api (FastAPI do FOMO, SQLite em volume)
        └─ veritai-ponte  ← só o fomo-api alcança (NetworkPolicy)
                │ S3 + InvokeEndpointAsync (usuário IAM só da ponte)
                ▼
SageMaker, endpoint assíncrono veritai-v0 (ml.m5.large, de 0 a 1 instância)
        └─ imagem veritai do ECR, perfil v0, modelos dentro da imagem
```

- **Por que assíncrono:** o endpoint de tempo real do SageMaker corta a resposta em 60 s, e uma análise com busca na web pode passar disso. O assíncrono aceita até 15 min por pedido e desliga a instância quando a fila fica vazia por uns 15 min. O próximo pedido liga a instância de novo, e essa primeira análise demora alguns minutos.
- **Por que a ponte:** o FOMO continua chamando `POST /analisar`, como faz com a VeritAI local; só muda o `VERITAI_URL`. A ponte não fica exposta na internet.
- **Credenciais:** só a ponte tem credencial da AWS. Ela usa um usuário IAM com o mínimo necessário (chamar o endpoint e usar o prefixo `veritai/` do bucket), e a chave fica num Secret do Kubernetes montado só no pod dela. A EC2 usa IMDSv2 com limite de 1 salto, então nenhum pod alcança o papel da instância. Esse papel só lê os segredos no boot e dá acesso pelo Session Manager.
- **Contrato:** não muda. No SageMaker, o container responde em `/ping` e `/invocations`, que fazem o mesmo que `/health` e `/analisar`.
- **Porcentagem:** nada muda na regra. O endpoint usa o perfil v0, sem calibração, então `porcentagem` continua `null`.

## Custos aproximados (us-east-1)

Valores de referência; confira na [calculadora da AWS](https://calculator.aws/) antes de criar.

| Recurso | Quando cobra | Aproximado |
|---|---|---|
| EC2 t3.medium + disco de 30 GB + IP fixo | enquanto a EC2 estiver ligada (disco e IP sempre) | US$ 0,05/h, cerca de US$ 36/mês ligada o mês todo |
| SageMaker ml.m5.large | só com a instância ligada (vai a zero sozinha) | cerca de US$ 0,12/h |
| Secrets Manager (3 segredos: chaves do publisher e do revisor e a chave da ponte), ECR, S3, CloudFront, alarme | sempre, valores pequenos | cerca de US$ 2/mês |

Com `pausar` (EC2 desligada) e o endpoint em zero, sobram o disco, o IP e os itens pequenos: alguns dólares por mês. `destruir` zera tudo.

## Passo a passo manual

### 1. Conta AWS (uma vez)

1. Crie a conta e ative o **MFA** no usuário raiz (menu da conta → Security credentials).
2. Não use o usuário raiz no dia a dia. Em **IAM Identity Center**, crie um usuário para você com o conjunto de permissões `AdministratorAccess`.
3. Peça cota do SageMaker: em **Service Quotas → Amazon SageMaker**, procure `ml.m5.large for endpoint usage` e peça o valor 1. Contas novas costumam ter zero, e sem essa cota a pilha falha ao criar o endpoint.
4. Escolha a região `us-east-1` (Norte da Virgínia) para todos os passos.

### 2. AWS CLI no Windows (uma vez)

```powershell
winget install Amazon.AWSCLI
aws configure sso          # use o portal do IAM Identity Center e a região us-east-1
aws sso login
aws sts get-caller-identity
```

O último comando deve mostrar a sua conta. As credenciais ficam no perfil do AWS CLI, nunca em arquivo do repositório.

### 3. GitHub (depois do push deste código)

1. O workflow **CI** publica `ghcr.io/fomo-veritai/veritai-ponte`. Em **github.com/orgs/FOMO-veritAI/packages**, abra o pacote, vá em **Package settings → Change visibility → Public**. Faça o mesmo com `fomo-api`, quando o FOMO publicar a imagem dele. Sem isso, o k3s não consegue baixar as imagens.
2. No repositório veritai, em **Settings → Pages → Source**, escolha **GitHub Actions**. O site de embeddings sai em `https://fomo-veritai.github.io/veritai/`.

### 4. Deploy

Na pasta do repositório veritai, com o Docker Desktop aberto:

```powershell
.venv\Scripts\python -m pip install -e ".[aws]"
.venv\Scripts\python deploy\aws\implantar.py base --email SEU_EMAIL --orcamento 20
.venv\Scripts\python deploy\aws\implantar.py imagem
.venv\Scripts\python deploy\aws\implantar.py app
```

| Comando | O que faz |
|---|---|
| `base` | Cria o alerta de orçamento, o repositório no ECR e o bucket. Confirme a inscrição no e-mail do alerta, se ele chegar. |
| `imagem` | Constrói a imagem com os modelos v0 dentro (cerca de 3,7 GB) e envia ao ECR, com o commit atual como tag. Recusa se houver mudança sem commit. |
| `app` | Cria o SageMaker, a EC2 com k3s e o CloudFront, de 15 a 30 min. No fim, mostra a `UrlApi`. |

`app` usa o `deploy/k8s/fomo.yaml` do branch `main` no GitHub. Ele precisa estar no `main` antes.

### 5. Ligar o frontend

1. No repositório **fomo**, em **Settings → Secrets and variables → Actions → Variables**, crie `FOMO_API_URL` com a `UrlApi` (por exemplo, `https://d1234abcd.cloudfront.net`).
2. Em **Actions → Frontend no GitHub Pages → Run workflow**, rode o workflow de novo.
3. Veja as chaves do publisher e do revisor só no seu terminal: `.venv\Scripts\python deploy\aws\implantar.py chaves`. Não cole as chaves em mensagens nem em arquivos.

### 6. Conferir

```powershell
.venv\Scripts\python deploy\aws\implantar.py status
```

- Abra `https://fomo-veritai.github.io/fomo/`.
- Crie uma notícia com a chave do publisher e peça a verificação. Com o endpoint em zero, a primeira análise demora de 5 a 10 min.
- Aprove a notícia com a chave do revisor.

### 7. Depois da demonstração

```powershell
.venv\Scripts\python deploy\aws\implantar.py pausar      # desliga a EC2; volta com: retomar
.venv\Scripts\python deploy\aws\implantar.py destruir --sim   # apaga tudo, inclusive o banco do FOMO
```

## Acesso ao cluster

Não há SSH nem porta 22 aberta. Para entrar na EC2, instale o [plugin do Session Manager](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) e rode:

```powershell
aws ssm start-session --target ID_DA_INSTANCIA
sudo k3s kubectl -n fomo get pods
```

## Kubernetes local

`deploy/k8s/fomo.yaml` também roda num k3s local. Por exemplo, com `docker run --privileged rancher/k3s`, crie antes os Secrets `fomo-chaves` e `veritai-ponte-aws` e o ConfigMap `veritai-aws`; a lista está no cabeçalho do arquivo. Sem uma conta AWS, a ponte sobe e responde `/health`, mas as análises voltam com erro 502.

## Limites conhecidos

- **Uma réplica e SQLite:** é uma instância de demonstração, não de produção.
- **Chaves compartilhadas:** as chaves do FOMO são as mesmas para todos os publishers e revisores, como no MVP local.
- **Chave da ponte de longa duração:** ela só é lida no boot da EC2. Para trocá-la, apague a pilha `veritai-app` no console do CloudFormation e rode `implantar.py app` de novo (o banco do FOMO na EC2 se perde).
- **HTTP entre o CloudFront e a EC2:** o trecho do CloudFront até a EC2 é HTTP, aceito só de IPs do CloudFront. O trecho até o navegador é HTTPS.
- **Primeira análise depois de um tempo parado:** demora porque liga a instância do SageMaker. O `VERITAI_TIMEOUT` do FOMO no cluster é 1300 s por isso.
