# NFSe Downloader - TSartExtractor

Aplicação desktop para Windows destinada à **consulta, sincronização, armazenamento e exportação de NFS-e do padrão nacional**, utilizando o **Ambiente de Dados Nacional (ADN)** e autenticação por certificado digital A1.

O projeto atende principalmente a **contadores, escritórios de contabilidade, departamentos fiscais e desenvolvedores** que necessitam obter documentos fiscais eletrônicos em lote, manter uma cópia local das informações e automatizar rotinas de consulta e arquivamento.

A aplicação não possui vínculo institucional com a Receita Federal, municípios ou com o ADN.

---

## 1. Visão geral

O NFSe Downloader realiza a comunicação direta com os serviços do ADN, utilizando **mTLS (mutual TLS)** para autenticação mediante certificado digital A1.

A aplicação permite:

* localizar certificados A1 (`.pfx` / `.p12`) disponíveis no computador;
* autenticar-se no ADN utilizando o certificado digital;
* consultar documentos fiscais eletrônicos associados ao CNPJ do certificado;
* sincronizar documentos incrementalmente por NSU;
* armazenar os dados localmente em SQLite;
* consultar as notas já sincronizadas sem realizar nova consulta ao ADN;
* filtrar documentos por período;
* visualizar o XML das notas;
* exportar os XMLs;
* gerar o DANFSe em PDF a partir do XML autorizado;
* exportar documentos selecionados em lote;
* gerar relatórios consolidados em PDF.

O objetivo é manter uma **base local das NFS-e sincronizadas**, reduzindo consultas repetitivas ao ambiente nacional e permitindo operações posteriores sobre documentos já armazenados.

---

## 2. Público-alvo

### Escritórios de contabilidade

Pode ser utilizado para centralizar a obtenção de NFS-e de clientes que utilizam certificados digitais A1, permitindo:

* sincronização periódica dos documentos;
* organização local dos XMLs;
* geração de DANFSe;
* exportação em lote para arquivo ou sistemas internos;
* geração de relatórios para conferência contábil e fiscal.

### Departamentos fiscais e financeiros

A aplicação pode auxiliar processos de:

* conferência de documentos recebidos e emitidos;
* arquivamento;
* levantamento de documentos por período;
* preparação de informações para escrituração;
* auditorias e conferências internas.

### Desenvolvedores

O projeto também pode ser utilizado como referência para integração com o ADN, principalmente nos seguintes pontos:

* autenticação mTLS com certificado A1;
* distribuição de documentos por NSU;
* processamento dos envelopes retornados pelo ADN;
* parsing de XML de NFS-e;
* persistência incremental;
* geração local de DANFSe;
* integração entre uma API fiscal e uma aplicação desktop.

---

# 3. Requisitos

## Sistema operacional

* Windows 10
* Windows 11

## Python

* Python 3.10 ou superior

Recomenda-se utilizar uma versão estável do Python disponível em:

[Python Downloads](https://www.python.org/downloads/?utm_source=chatgpt.com)

Durante a instalação do Python no Windows, recomenda-se habilitar **Add Python to PATH**.

## Certificado digital

Atualmente são suportados certificados:

* A1 em arquivo `.pfx`;
* A1 em arquivo `.p12`.

É necessária a senha correspondente ao certificado.

### Certificados A3

Certificados A3 armazenados em:

* token USB;
* cartão criptográfico;
* outros dispositivos físicos;

não são suportados atualmente.

O projeto trabalha diretamente com certificados que podem ser carregados como arquivo pelo cliente HTTP/mTLS.

---

# 4. Instalação

Clone o repositório:

```bat
git clone https://github.com/wh0am-i/TSartExtractor.git
cd TSartExtractor
```

Crie um ambiente virtual:

```bat
python -m venv .venv
```

Ative o ambiente:

```bat
.venv\Scripts\activate
```

Instale as dependências:

```bat
pip install -r requirements.txt
```

Execute a aplicação:

```bat
python main.py
```

Também pode ser utilizado o arquivo `.bat` disponibilizado na raiz do projeto para automatizar essas etapas.

---

# 5. Fluxo operacional

## 5.1. Configuração do certificado

O aplicativo localiza certificados A1 disponíveis no ambiente e solicita a senha necessária para utilização da chave privada.

O certificado é utilizado para estabelecer a conexão mTLS com os serviços do ADN.

A senha não deve ser armazenada no código-fonte nem versionada no repositório.

---

## 5.2. Sincronização

A sincronização utiliza a distribuição de documentos do ADN por **NSU (Número Sequencial Único)**.

O fluxo principal utiliza:

```text
GET /contribuintes/DFe/{NSU}
```

Os documentos são processados em páginas de até 50 registros.

O sistema mantém o último NSU processado separadamente para cada CNPJ, permitindo que novas sincronizações sejam incrementais.

Em uma execução posterior, o aplicativo pode continuar a partir do último NSU armazenado, evitando a necessidade de processar novamente todo o histórico local.

Fluxo simplificado:

```text
Certificado A1
     │
     ▼
Autenticação mTLS
     │
     ▼
ADN
     │
     ▼
DFe/{NSU}
     │
     ▼
Envelope de documentos
     │
     ▼
Parsing
     │
     ▼
Persistência SQLite
     │
     ▼
Base local de NFS-e
```

---

# 6. Obtenção dos XMLs

Quando o documento distribuído não contém todas as informações necessárias para armazenamento local, o aplicativo realiza a consulta individual correspondente.

A obtenção do documento utiliza a chave de acesso da NFS-e:

```text
/nfse/{chave}
```

O XML autorizado é armazenado localmente e utilizado posteriormente pelas funcionalidades de visualização, exportação e geração do DANFSe.

---

# 7. Geração do DANFSe

O PDF é gerado localmente a partir do XML autorizado da NFS-e.

A aplicação não depende da disponibilidade de uma API externa específica para geração do PDF.

O módulo responsável encontra-se em:

```text
tsart_extractor/services/danfse.py
```

Essa abordagem também permite manter o XML como documento fiscal principal e tratar o PDF como uma representação derivada.

---

# 8. Persistência local

Os dados sincronizados são armazenados em SQLite.

Banco padrão:

```text
%USERPROFILE%\.nfse_downloader\notas.db
```

A persistência local permite:

* evitar consultas desnecessárias ao ADN;
* manter o histórico já sincronizado;
* consultar documentos anteriormente processados;
* acompanhar o NSU de cada CNPJ;
* trabalhar com os documentos mesmo após a sincronização inicial.

A base SQLite deve ser considerada parte importante do armazenamento local do usuário e deve ser incluída nas rotinas de backup quando utilizada em ambiente profissional.

---

# 9. Diretórios de saída

Os arquivos gerados pela aplicação são armazenados por categoria:

```text
%USERPROFILE%\.nfse_downloader\
├── xml\
├── pdf\
└── relatorios\
```

### XML

Contém os documentos XML das NFS-e sincronizadas ou obtidas individualmente.

### PDF

Contém os respectivos DANFSe gerados a partir dos XMLs.

### Relatórios

Contém os relatórios consolidados gerados pela aplicação.

---

# 10. Relatórios

O módulo de relatórios utiliza **ReportLab** para gerar PDFs consolidados.

Os relatórios podem apresentar informações como:

* CNPJ;
* identificação do tomador;
* identificação do documento;
* datas;
* valores;
* demais informações disponíveis no documento processado;
* totais consolidados.

O relatório deve ser tratado como ferramenta de conferência e organização. O XML autorizado permanece como fonte documental principal para fins de processamento.

---

# 11. Arquitetura

O projeto utiliza uma arquitetura modular, separando interface, serviços, persistência e integração com o ADN.

```text
main.py
│
└── tsart_extractor/
    │
    ├── application.py
    │
    ├── core/
    │   ├── settings.py
    │   └── app_logging.py
    │
    ├── persistence/
    │   └── repository.py
    │
    ├── services/
    │   ├── api/
    │   │   ├── client.py
    │   │   ├── envelope.py
    │   │   └── parser.py
    │   │
    │   ├── certificates.py
    │   ├── danfse.py
    │   └── reporting.py
    │
    └── ui/
        ├── app.py
        ├── layout.py
        ├── certificates.py
        ├── synchronization.py
        ├── notes.py
        ├── exports.py
        ├── events.py
        ├── progress_dialog.py
        └── windowing.py
```

---

# 12. Componentes

## `core/`

Configurações globais e infraestrutura básica da aplicação.

### `settings.py`

Centraliza configurações como:

* ambiente do ADN;
* URLs;
* diretórios;
* parâmetros de execução.

### `app_logging.py`

Configuração centralizada de logging.

---

## `persistence/`

Responsável pelo armazenamento local.

### `repository.py`

Implementa a comunicação com o SQLite e as operações relacionadas ao modelo de documentos armazenados.

---

## `services/api/`

Camada de comunicação e processamento da API do ADN.

### `client.py`

Responsável pelo transporte HTTP e configuração de mTLS.

### `envelope.py`

Responsável pela leitura e interpretação dos envelopes retornados pelo ADN.

### `parser.py`

Responsável pelo parsing dos XMLs de NFS-e e eventos.

A separação entre envelope e documento fiscal evita acoplar o processamento da resposta HTTP à estrutura interna da NFS-e.

---

## `services/certificates.py`

Responsável pela descoberta e manipulação dos certificados A1 disponíveis no ambiente.

---

## `services/danfse.py`

Responsável pela geração local do DANFSe a partir do XML autorizado.

---

## `services/reporting.py`

Responsável pela geração dos relatórios PDF.

---

## `ui/`

Implementação da interface gráfica utilizando Tkinter.

A interface não contém diretamente as regras principais de comunicação com o ADN ou de processamento fiscal. Essas responsabilidades ficam nas camadas de serviço.

---

# 13. Ambiente de testes

O projeto possui suporte ao ambiente de testes/restrito do ADN.

Para utilizar esse ambiente, altere:

```python
AMBIENTE = "restrita"
```

em:

```text
tsart_extractor/core/settings.py
```

O ambiente deve ser utilizado para desenvolvimento e validação antes de operações contra o ambiente de produção, quando aplicável.

---

# 14. Limitações conhecidas

## 14.1. Escopo da distribuição por NSU

A distribuição:

```text
/contribuintes/DFe/{NSU}
```

retorna documentos nos quais o CNPJ associado ao certificado figura como parte interessada, como prestador, tomador ou intermediário.

O comportamento exato em relação a todos os cenários de NFS-e deve ser validado de acordo com a documentação vigente do ADN.

Caso determinado documento não seja disponibilizado pela distribuição por NSU, pode ser necessário realizar consulta direta utilizando sua chave de acesso.

---

## 14.2. Retenção de documentos

A distribuição de documentos por NSU está sujeita às regras de retenção e disponibilidade definidas pelo ambiente nacional.

Consequentemente, não se deve considerar o mecanismo de distribuição como substituto de uma política própria de arquivamento.

Para utilização profissional, recomenda-se:

1. realizar sincronizações periódicas;
2. manter cópia dos XMLs;
3. realizar backup da base SQLite;
4. manter uma política própria de retenção documental.

O banco local funciona como histórico dos documentos que já foram efetivamente sincronizados pela aplicação.

---

## 14.3. Certificados A3

O suporte a certificados A3 não está implementado.

O projeto atualmente depende da disponibilidade da chave privada em um arquivo de certificado A1.

---

# 15. Segurança

O certificado digital utilizado pelo aplicativo possui uma chave privada e deve ser tratado como uma credencial sensível.

Recomenda-se:

* não versionar certificados;
* não armazenar senhas em arquivos de configuração;
* não compartilhar arquivos `.pfx` ou `.p12`;
* restringir as permissões da pasta onde o certificado é armazenado;
* utilizar certificados exclusivamente em máquinas confiáveis;
* realizar backup dos documentos fiscais em armazenamento apropriado.

O repositório não deve conter certificados reais, senhas ou documentos fiscais de produção.

Arquivos de diagnóstico eventualmente armazenados em:

```text
var/debug/
```

também devem ser avaliados antes de serem compartilhados, pois respostas da API podem conter informações fiscais.

---

# 16. Logs e diagnóstico

Logs da aplicação:

```text
var/logs/
```

Arquivos de diagnóstico:

```text
var/debug/
```

Os arquivos de diagnóstico são destinados principalmente ao desenvolvimento e investigação de problemas de integração.

Antes de publicar ou enviar esses arquivos para terceiros, deve-se verificar se eles contêm:

* CNPJ;
* dados de tomadores;
* chaves de acesso;
* XMLs;
* dados de certificados;
* outras informações fiscais.

---

# 17. Desenvolvimento

O projeto utiliza:

* Python;
* Tkinter;
* SQLite;
* HTTP/mTLS;
* XML;
* ReportLab.

A aplicação procura manter as responsabilidades separadas entre:

```text
Interface
    ↓
Serviços
    ↓
Integração / processamento
    ↓
Persistência
```

O ponto de entrada da aplicação é:

```text
main.py
```

Ele é responsável apenas pela inicialização, enquanto a composição da aplicação ocorre em:

```text
tsart_extractor/application.py
```

---

# 18. Testes e validação

Os arquivos relacionados a validações manuais encontram-se em:

```text
tests/
```

Testes envolvendo documentos fiscais reais devem ser realizados preferencialmente com dados anonimizados ou em ambiente de testes quando possível.

---

# 19. Contribuição

Contribuições são bem-vindas.

Áreas particularmente relevantes incluem:

* suporte a diferentes estruturas de XML;
* melhoria do parser;
* tratamento de erros da API;
* mecanismos de retry;
* testes automatizados;
* suporte a diferentes cenários de distribuição;
* melhorias na geração do DANFSe;
* suporte futuro a certificados A3;
* melhorias na persistência e recuperação de sincronização;
* documentação da integração com o ADN.

Ao abrir uma issue, forneça, sempre que possível:

* versão do projeto;
* versão do Python;
* ambiente utilizado;
* descrição do problema;
* trecho relevante do log;
* resposta sanitizada da API, quando aplicável.

Não publique certificados, senhas ou documentos fiscais contendo dados reais.

---

# 20. Estrutura dos arquivos

```text
TSartExtractor/
├── main.py
├── requirements.txt
├── tsart_extractor/
│   ├── application.py
│   ├── core/
│   ├── persistence/
│   ├── services/
│   └── ui/
├── assets/
│   └── images/
├── var/
│   ├── debug/
│   └── logs/
└── tests/
```

---

# 21. Considerações fiscais e operacionais

O NFSe Downloader é uma ferramenta de apoio à obtenção e organização de documentos fiscais eletrônicos.

A aplicação **não substitui sistemas contábeis, fiscais ou de escrituração**, nem realiza por conta própria a validação fiscal ou tributária das informações contidas nos documentos.

Antes de utilizar os dados para:

* escrituração;
* apuração tributária;
* obrigações acessórias;
* auditoria;
* entrega ao cliente;
* integração com sistemas contábeis;

é responsabilidade do usuário validar a integridade e a adequação dos documentos obtidos.

---

# 22. Aviso legal

Este projeto é independente e não possui vínculo oficial com:

* Receita Federal do Brasil;
* administrações tributárias municipais;
* Ambiente de Dados Nacional (ADN);
* demais órgãos responsáveis pela infraestrutura fiscal nacional.

As APIs e os formatos utilizados podem sofrer alterações conforme a evolução dos serviços oficiais.

O projeto é disponibilizado para fins de automação, integração e processamento de documentos fiscais, cabendo ao usuário avaliar sua adequação ao ambiente operacional e às suas obrigações legais.

---

## Licença

Consulte o arquivo `LICENSE` do repositório para obter as condições de uso e distribuição do projeto.
