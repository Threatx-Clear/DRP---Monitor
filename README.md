# Monitor DRP

Piloto local para pesquisa passiva de uma empresa. A tela principal pede somente o nome; o app gera termos, usernames e dois candidatos de domínio sem exigir chave de API.

## O que é consultado

- **Sherlock:** testa até quatro usernames derivados do nome em Instagram, LinkedIn, TikTok, X/Twitter, YouTube, Telegram e Mercado Livre. É uma verificação de nomes exatos, não uma busca por todos os perfis que mencionam a empresa.
- **Cert Spotter / Certificate Transparency:** consulta certificados para os candidatos `<nome>.com.br` e `<nome>.com`. Esses domínios são palpites gerados pelo nome e não são tratados como domínios oficiais.
- **URLScan:** procura até 100 registros públicos existentes por termo com títulos relacionados ao nome e consulta os domínios candidatos e até 96 variações de typosquatting (até 192 combinações `.com.br`/`.com`), em lotes de até 48 domínios. Termos duplicados apenas por maiúsculas/minúsculas são consultados uma vez. O app não abre nem interage com os sites encontrados.
- **Anúncios:** oferece atalhos separados dos achados para pesquisar o nome nas bibliotecas públicas da Meta e do Google. São links para pesquisa manual, não anúncios coletados; sem uma API autorizada, o app não importa anúncios.
- **Documentos:** oferece atalhos separados dos achados para pesquisar PDFs indexados no Google e no Bing (`filetype:pdf`) e documentos no Scribd. Os documentos e resultados dessas buscas não são importados automaticamente.

A lista de achados e o CSV contêm somente resultados das fontes consultadas. O relatório HTML mantém os atalhos em uma seção própria, separada da tabela de achados. Na tabela, cada achado pode ser classificado como pendente, relevante, falso positivo ou validado, com uma observação do analista; a classificação e a observação seguem nos downloads CSV e HTML. Também é possível selecionar vários registros e aplicar a mesma classificação em lote. A observação compartilhada em lote é opcional: se informada, substitui as observações dos selecionados; se ficar vazia, as observações individuais são mantidas. A revisão fica guardada na sessão atual e não é persistida após encerrar o app.

Facebook, OLX e Shopee não são cobertos pelo verificador Sherlock incluído neste piloto. Os resultados variam por fonte e podem conter falsos positivos. A presença de um username ou certificado não confirma vínculo com a empresa nem indica fraude.

## Limites e uso responsável

As consultas são passivas e limitadas a fontes públicas; não há login, proxy, CAPTCHA ou contorno de controles de acesso. Sherlock consulta diretamente as páginas públicas dos serviços selecionados. Respeite os termos e limites de cada plataforma.

O nome sozinho não identifica com certeza a razão social, domínio oficial, aliases ou usernames. O piloto usa variações determinísticas locais — sem LLM e sem custo por token — e exibe esses candidatos para revisão. Gera até 96 alterações por remoção, transposição, duplicação, substituição visual, teclas vizinhas e inserção de tecla próxima, combinadas com `.com.br` e `.com`; consulta somente registros existentes no URLScan e não registra nem visita os domínios. A consulta por título pede até 100 registros por termo (máximo documentado pela API); pode retornar menos se não houver correspondências suficientes, e as cotas do URLScan limitam a frequência e o volume total. As consultas de domínio são divididas em lotes de até 48 domínios; no máximo, 194 domínios (incluindo os dois candidatos-base) requerem cinco chamadas. Se uma fonte responder com HTTP 429, o app informa o limite e recomenda aguardar antes de pesquisar novamente. A pesquisa de documentos é aberta manualmente nos serviços indicados; não há raspagem nem coleta automática de resultados do Google, Bing ou Scribd.

O limite de quatro usernames e sete sites reduz o volume para o piloto. Mesmo assim, o Sherlock pode demorar ou ser limitado pelas plataformas. Não é uma configuração para varredura massiva de 300 empresas sem avaliar termos, limites, cobertura e volume primeiro.

## Instalação

Requisitos: Windows PowerShell, Python e `uv` disponível no PATH. Na pasta do projeto:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\setup_sherlock.ps1
```

O Sherlock fica em um ambiente separado com Python 3.14 porque a versão atual do Sherlock depende de pandas anterior à versão 3, enquanto o Python 3.15 instalado no ambiente principal não tem wheel compatível para essa versão do pandas. O restante do app continua usando `.venv`.

## Publicar no Streamlit Community Cloud

O projeto está preparado para instalar o Sherlock no Linux do Community Cloud; no Windows local, ele continua usando o ambiente isolado criado pelo script acima. No deploy Linux, as dependências selecionam `pandas<3` e Sherlock 0.16.2.

1. Crie um repositório **privado e vazio** no GitHub (sem README, licença ou `.gitignore` inicial). Esta pasta ainda não é um repositório Git. No PowerShell, dentro da pasta do projeto, inicialize e envie os arquivos:

   ```powershell
   git init -b main
   git add .
   git commit -m "Prepare app for Streamlit Community Cloud"
   git remote add origin https://github.com/SEU-USUARIO/NOME-DO-REPOSITORIO.git
   git push -u origin main
   ```

   Troque a URL pelo endereço do repositório criado. Não envie `.venv`, `.sherlock-venv` nem `.streamlit/secrets.toml`; eles já estão ignorados pelo `.gitignore`.
2. Entre em [share.streamlit.io](https://share.streamlit.io), conecte o GitHub e escolha **Create app** → **Yup, I have an app**.
3. Selecione o repositório privado, a branch `main` e o arquivo `app.py` na raiz.
4. Em **Advanced settings**, escolha Python **3.12** e confirme o deploy.
5. Em **App settings** → **Sharing**, mantenha a aplicação privada e convide somente os colegas autorizados.

O Community Cloud precisa de acesso ao repositório privado e exige permissão administrativa no GitHub para criar a chave de deploy. A documentação atual informa limite de um app privado por conta. Esta aplicação não tem autenticação própria: não torne o app público para uso interno.

As consultas são feitas pelo servidor hospedado, compartilhando as cotas das fontes entre os usuários. URLScan e Cert Spotter podem responder HTTP 429; a hospedagem não aumenta essas cotas. As revisões e observações ficam apenas na sessão e não são persistidas. Consulte os logs do app no Community Cloud caso o build ou o Sherlock falhe.

## Executar

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Digite o nome da empresa e clique em **Pesquisar**. A interface oferece filtros por categoria, triagem individual ou em lote dos achados, status das fontes, atalhos de pesquisa manual e downloads CSV/HTML. Para classificar em lote, marque os registros na coluna **Selecionar**, escolha a classificação e clique em **Aplicar**. O tema claro/escuro pode ser alterado pelo menu do Streamlit.

## Testes

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
