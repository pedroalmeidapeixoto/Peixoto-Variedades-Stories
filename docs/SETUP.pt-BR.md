# Configuração passo a passo (Peixoto Variedades)

Faça na ordem. **Nunca cole tokens ou senhas em chats**: cada valor vai direto em *Settings → Secrets and variables → Actions* do GitHub, ou no terminal.

## 1. Instagram e Meta

1. A conta do Instagram **Peixoto Variedades** precisa ser **Profissional** (Comercial ou Criador): *Configurações → Tipo de conta e ferramentas → Mudar para conta profissional*.
2. Ligue-a a uma **Página do Facebook** (crie uma se não existir): *Central de Contas → Contas conectadas* ou *Editar perfil → Página*.
3. Meta for Developers ([developers.facebook.com](https://developers.facebook.com)):
   - Pode **reaproveitar o mesmo app do Grande Mestre**. Ao gerar o token, é só selecionar a Página e a conta do Instagram da Peixoto Variedades.
   - Se preferir um app novo: *Criar app → Business* e adicione o caso de uso *Gerenciar mensagens e conteúdo no Instagram*.
4. No [Explorador da Graph API](https://developers.facebook.com/tools/explorer), gere um token de usuário com as permissões `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, **escolhendo a Página e a conta do Instagram da Peixoto Variedades**.
5. No seu computador, na pasta do projeto:

   ```bash
   pip install -e .
   stories-token
   ```

   Informe App ID, App Secret e o token curto. Ele imprime o `IG_USER_ID` e o `IG_ACCESS_TOKEN` (válido ~60 dias). Guarde-os para o passo 4 e limpe o terminal.

### 1-B. Alternativa sem Página: login do Instagram

Use se o vínculo da Página com o Instagram não funcionar (por exemplo, conta comercial da Meta restrita). Só exige que a conta do Instagram seja profissional. Os nomes dos menus da Meta mudam de vez em quando, então procure por equivalentes.

1. No app da Meta, abra o caso de uso *Gerenciar mensagens e conteúdo no Instagram* e escolha **Configuração da API com login do Instagram**. Confira que as permissões `instagram_business_basic` e `instagram_business_content_publish` estão disponíveis.
2. Adicione a conta como **testadora** do app: *Funções do app → Funções → Adicionar pessoas → Testadores do Instagram*, com o usuário `@peixotovariedadescg`.
3. No Instagram da loja (app ou web), aceite o convite: *Configurações → Apps e sites → Convites de testador*.
4. De volta à configuração da API com login do Instagram, em **Gerar tokens de acesso**, adicione a conta e clique em **Gerar token**. Copie o token.
5. No terminal, na pasta do projeto:

   ```bash
   .venv\Scripts\stories-token-ig.exe
   ```

   Cole o token. Se informar o App Secret, ele troca por um token de 60 dias; se deixar em branco, tenta renovar o token. No final imprime o `IG_USER_ID` e o `IG_ACCESS_TOKEN`.
6. No GitHub, cadastre `IG_USER_ID` e `IG_ACCESS_TOKEN` como secrets e crie a **variável** (não secret) `IG_API` com o valor `instagram`, em *Settings → Secrets and variables → Actions → Variables*.

Renovação: antes dos 60 dias, rode `stories-token-ig` de novo, cole o token atual e deixe o App Secret em branco para renovar (o token precisa ter mais de 24 h). Atualize o secret `IG_ACCESS_TOKEN`.

## 2. Cloudflare R2 (onde ficam as artes)

1. Crie uma conta em [dash.cloudflare.com](https://dash.cloudflare.com) e abra **R2 Object Storage**. O R2 pede um cartão cadastrado, mas o plano grátis (10 GB) basta: as artes são apagadas depois de postadas.
2. **Create bucket**, por exemplo `peixoto-stories`.
3. No bucket, *Settings → Public access*: habilite o domínio **r2.dev** (ou conecte um domínio seu). Copie a URL pública, ex. `https://pub-xxxx.r2.dev`: é o `R2_PUBLIC_URL`.
4. Em *R2 → Manage R2 API Tokens → Create API token*: permissão **Object Read & Write**, restrito a esse bucket. Copie **Access Key ID** e **Secret Access Key** (o segredo só aparece uma vez).
5. O **Account ID** aparece na página inicial do R2.

Você terá: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_URL`.

## 3. Telegram (receber artes e avisos)

1. No Telegram, fale com [@BotFather](https://t.me/BotFather), `/newbot`, e dê um nome (ex. *Peixoto Variedades Stories*). Guarde o token: é o `TELEGRAM_BOT_TOKEN`. Use **um bot novo**, separado do bot do restaurante.
2. Mande qualquer mensagem para o bot novo e abra `https://api.telegram.org/bot<TOKEN>/getUpdates` no navegador. O número em `"chat":{"id":...}` é o `TELEGRAM_CHAT_ID`.

## 4. GitHub

1. Crie um repositório **público** `Peixoto-Variedades-Stories` e envie este projeto (público porque só guarda a fila, e assim o Actions é ilimitado).
2. *Settings → Secrets and variables → Actions → New repository secret*, cadastre:
   `IG_USER_ID`, `IG_ACCESS_TOKEN`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_PUBLIC_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
3. *Settings → Actions → General → Workflow permissions*: **Read and write permissions**.

## 5. Gatilho externo (a cada 3 minutos)

O bot publica **uma arte por execução**, então o gatilho precisa ser mais frequente que o intervalo do `pace.json`, de preferência um divisor dele. Com `interval_minutes` em 6 (o ritmo atual), use **3 minutos**: a arte seguinte sai exatamente 6 minutos depois da anterior. Regra geral: o gatilho é metade do intervalo (6 → 3, 8 → 2, 10 → 5). Um gatilho que não divide o intervalo estica o ritmo (por exemplo, 6 com gatilho de 4 minutos viraria 8). Com intervalos de 20 minutos ou mais, 5 a 10 minutos bastam. O cron do próprio GitHub atrasa horas. Use o [cron-job.org](https://cron-job.org) (grátis) para disparar o workflow:

1. GitHub → *Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate*:
   - *Repository access*: **Only select repositories** → só `Peixoto-Variedades-Stories`;
   - *Permissions → Repository → Actions*: **Read and write**.
2. No cron-job.org, **Create cronjob**:
   - URL: `https://api.github.com/repos/<seu-usuario>/Peixoto-Variedades-Stories/actions/workflows/stories.yml/dispatches`
   - Schedule: **a cada 3 minutos** (o cron-job.org grátis aceita intervalos de 1 minuto)
   - *Advanced*: método **POST**; cabeçalhos `Authorization: Bearer <token fine-grained>`, `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`; corpo `{"ref":"main"}`.
3. Resposta esperada: **204**.

## 6. Primeiro teste

1. Confira o ritmo em `pace.json` (atual: segunda a sábado, das 08:00 às 20:00, uma arte a cada 6 minutos, até 89 por dia).
2. Mande **uma** arte 1080×1920 ao bot como **arquivo**. Em poucos minutos ele responde "Recebi 1 arte(s)". Se ela for postada ao vivo, ela aparece no Instagram da loja; por isso teste com uma arte que você realmente queira publicar.
3. `/fila` mostra o que está na fila; `/remover N` tira um item.

## 7. Colocar o pacote diário no ar

O `pace.json` está no modo de rodízio (`"recycle": true`): cada arte volta ao fim da fila depois de postada e continua guardada no R2, então o mesmo pacote roda todo dia, de segunda a sábado, das 08:00 às 20:00, uma arte a cada 6 minutos (as 89 artes saem entre 08:00 e ~16:54).

1. Gatilho do passo 5 funcionando (a cada 3 minutos).
2. No Telegram, abra o bot, clique no clipe, escolha **Arquivo** e selecione **todas** as imagens da pasta `arts/pacote` (a numeração `01-…` a `89-…` define a ordem). Confirme o envio **como arquivos**, sem comprimir.
3. Em alguns minutos o bot responde "Recebi N arte(s)". Mande `/fila` para conferir o total.
4. Faça isso **fora do horário** (por exemplo, no domingo ou à noite): as artes só entram na fila e a primeira sai na abertura seguinte, às 08:00.
5. Se o pacote crescer ou diminuir, ajuste `daily_limit` no `pace.json` para o número de artes, para o pacote inteiro sair uma vez por dia.

## Manutenção

- **Token do Instagram:** vence em ~60 dias. Quando vencer, o bot para, avisa no Telegram e preserva a fila. Gere outro (passo 1.4 e 1.5) e atualize o secret `IG_ACCESS_TOKEN`.
- **Fila acabando:** o bot avisa quando restam `low_queue` Stories e quando acaba.
- **Falhas:** uma arte que falha 3 vezes vai para `state/failed.json` e a fila segue; você é avisado no Telegram.
