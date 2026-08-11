# 📄 NFSe Downloader

**Chega de baixar nota por nota, clicando e clicando no portal do governo.**

Este é um app desktop para Windows que conecta no seu certificado digital, puxa **todas as suas NFS-e (Nota Fiscal de Serviço eletrônica, padrão nacional)** direto da fonte oficial, baixa os XMLs e PDFs (DANFSe) em lote e ainda monta um **relatório em PDF** prontinho, com totais, para você mandar pro contador ou guardar no arquivo.

Sem captcha. Sem clicar em nota por nota. Sem depender do site ficar no ar.

> 🔧 Sob o capô é um cliente Python que fala diretamente com a API oficial do **ADN (Ambiente de Dados Nacional)**, autenticado via **mTLS** com o seu certificado digital A1. Se você é dev e quer entender/mexer no código, a seção [Para desenvolvedores](#-para-desenvolvedores) é pra você.

---

## ✅ O que ele faz

- 🔍 Encontra sozinho seu certificado digital (`.pfx` / `.p12`) no seu computador
- 🔄 Sincroniza o histórico de notas com o ADN e guarda tudo localmente (não precisa buscar tudo de novo toda vez)
- 📅 Filtra as notas por período (data inicial/final)
- 👀 Mostra o XML de cada nota num painel lateral, antes mesmo de baixar
- ⬇️ Exporta XML + PDF (DANFSe) em lote das notas filtradas
- 📊 Gera um relatório PDF consolidado, com CNPJ, tomador, valores e totais no rodapé

---

## 💻 Requisitos

- **Windows 10 ou 11**
- **Python 3.10+** instalado ([python.org](https://www.python.org/downloads/) — marque a opção "Add Python to PATH" na instalação)
- Seu **certificado digital A1** (arquivo `.pfx` ou `.p12`) e a senha dele

> ⚠️ Certificado **A3** (aquele token físico/cartão) **não é suportado**. O programa só lê certificados em arquivo (A1).

---

## 🚀 Como usar (modo fácil — para qualquer pessoa)

1. Baixe ou clone este repositório no seu computador.
2. Dê **duplo clique no arquivo `.bat`** que está na raiz do projeto.
3. Aguarde — na primeira vez ele vai instalar as dependências automaticamente. Isso pode levar alguns minutos.
4. A janela do programa vai abrir. Digite a senha do certificado e clique em **Conectar**.
5. Clique em **Sincronizar** para buscar suas notas, filtre por data, e use os botões de download e relatório.

### 🛡️ O antivírus bloqueou o `.bat`?

Isso é normal e acontece bastante com scripts `.bat` desconhecidos (falso positivo). Duas opções:

- Adicione uma exceção para o arquivo/pasta no seu antivírus, **ou**
- Rode os mesmos comandos manualmente pelo terminal (não precisa confiar em nenhum executável extra):

```bat
:: Abra o Prompt de Comando (cmd) dentro da pasta do projeto e rode, na ordem:
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Isso faz exatamente o que o `.bat` faria: cria um ambiente virtual isolado, instala as dependências do `requirements.txt` e abre o programa. Nada de misterioso — é só automação de 4 comandos.

---

## 🧑‍💻 Para desenvolvedores

### Stack

- **Python puro**, sem frameworks pesados
- GUI em **Tkinter** (`tsart_extractor/ui/`) — nenhuma dependência de sistema extra além do que já vem com o Python no Windows
- Persistência local em **SQLite** (`tsart_extractor/persistence/`) — banco fica em `~/.nfse_downloader/notas.db`
- Autenticação **mTLS** e integração ADN em `tsart_extractor/services/api/`
- Geração de PDF com **ReportLab** em `tsart_extractor/services/reporting.py`

### Setup local

```bat
git clone https://github.com/wh0am-i/TSartExtractor.git
cd TSartExtractor
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### Estrutura do projeto

```text
main.py                              - ponto de entrada, sem regras de negócio
tsart_extractor/
├── application.py                  - inicialização da aplicação
├── core/
│   ├── settings.py                 - URLs, diretórios e ambiente
│   └── app_logging.py              - logging centralizado
├── persistence/
│   └── repository.py               - SQLite e modelo Nota
├── services/
│   ├── api/
│   │   ├── client.py               - transporte HTTP/mTLS
│   │   ├── envelope.py             - leitura do envelope ADN
│   │   └── parser.py               - parsing de XML de NFS-e/eventos
│   ├── certificates.py             - certificados A1/Windows Store
│   ├── danfse.py                    - DANFSe local a partir do XML autorizado
│   └── reporting.py                - relatório PDF
└── ui/
    ├── app.py                      - composição da janela
    ├── layout.py                   - layout e estilos
    ├── certificates.py             - fluxo de conexão
    ├── synchronization.py          - sincronização e progresso
    ├── notes.py                    - tabela e detalhes
    ├── exports.py                  - ZIP e relatório
    ├── events.py                   - eventos assíncronos
    ├── progress_dialog.py          - progresso modal e cancelamento
    └── windowing.py                - logo e janelas secundárias
assets/images/                       - imagens usadas pela interface
var/debug/                           - respostas e auditorias de diagnóstico
var/logs/                            - logs da aplicação
tests/                               - utilitários de validação manual
```

Arquivos baixados ficam em:

```
%USERPROFILE%\.nfse_downloader\xml
%USERPROFILE%\.nfse_downloader\pdf
%USERPROFILE%\.nfse_downloader\relatorios
```

### Fluxo técnico, por trás da GUI

1. **Sincronização**: consome `GET /contribuintes/DFe/{NSU}`, paginando de 50 em 50 documentos, e guarda separadamente o último NSU processado para cada CNPJ.
2. **Arquivos por nota**: usa `/nfse/{chave}` para obter XMLs ausentes e gera o DANFSe localmente a partir do XML autorizado, sem depender da antiga API pública de PDF.
3. **Parsing tolerante**: a leitura do envelope e dos XMLs fica separada em `services/api/envelope.py` e `services/api/parser.py`.
4. **Ambiente de testes**: para testar sem certificado de produção, troque `AMBIENTE = "restrita"` em `tsart_extractor/core/settings.py`.

### Quer contribuir?

Pull requests são bem-vindos, principalmente em cima dos pontos abaixo (veja também os avisos na seção seguinte).

---

## ⚠️ Avisos e limitações conhecidas

- **Escopo do `DFe/{NSU}`**: essa rota distribui os documentos em que o CNPJ do certificado é "parte interessada" (prestador, tomador ou intermediário). Segundo relatos de outros devs que já integraram, isso deve incluir as notas emitidas pelo próprio usuário como prestador — mas isso não está 100% explícito na documentação oficial. Se depois de sincronizar faltarem notas, abra uma issue; pode ser necessário complementar com consulta direta por chave de acesso.
- **Retenção de documentos**: como em outros ambientes fiscais nacionais (ex.: NF-e), a distribuição por NSU costuma ter uma janela de retenção. Notas mais antigas que essa janela podem não aparecer mais pelo DFe — por isso o `notas.db` funciona como sua base histórica, e vale sincronizar com uma certa regularidade.
- **Certificado A3 não suportado** — apenas A1 (arquivo).

---

## 📌 Resumo rápido

| Você é... | Faça isso |
|---|---|
| **Usuário comum** | Baixe o projeto, dê duplo clique no `.bat`, digite a senha do certificado e use a interface |
| **Antivírus bloqueou o `.bat`** | Abra o `cmd` na pasta do projeto e rode os 4 comandos manualmente (seção acima) |
| **Dev curioso** | Veja a seção [Para desenvolvedores](#-para-desenvolvedores) e a pasta `tsart_extractor/` |

---

*Projeto sem vínculo oficial com a Receita Federal, prefeituras ou o ADN. Use por sua conta e risco, sempre validando os dados antes de qualquer entrega fiscal ou contábil.*
