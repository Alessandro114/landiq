# Gaeta Serapo — Research Data (MVP Case Study)

**Compiled**: 11 April 2026
**Scope**: struttura ricettiva ~900 mq in loc. Serapo (Gaeta, LT) — analisi fattibilità trasformazione residenziale
**Method**: ricerca pubblica via WebSearch/WebFetch, no dati riservati. Ogni claim è citato.

---

## 1. Inquadramento urbanistico

### 1.1 Strumento vigente
- **PRG Gaeta**: il piano regolatore generale vigente è quello **approvato nel 1973** (oltre 50 anni fa), oggi in fase di revisione.
  Fonte: [Comune di Gaeta — "Piano Regolatore Generale: dopo 42 anni Gaeta cambia"](http://www.comune.gaeta.lt.it/News/Piano-Regolatore-Generale-dopo-42-anni-Gaeta-cambia)
- **Variante Generale al PRG**: in corso dal 2015 (Del. G.C. n. 191/2015). Incarico aggiudicato al RTP guidato da **Mate** (con prof. Stefano Stanghellini, arch. Francesco Nigro, Dream).
  Fonte: [mateng.it — Piano Regolatore Generale di Gaeta](https://mateng.it/it/piano-regolatore-generale-di-gaeta-lt-438.asp)
- **Documento preliminare di indirizzo** della variante pubblicato sul portale comunale, aggiornato al **20 settembre 2025**. Include 26 elaborati (zoning PRG vigente, studi geologici, agronomici, schema preliminare).
  Fonte: [Comune di Gaeta — Documenti pubblici / Doc. pre. di indirizzo variante PRG](https://www.comune.gaeta.lt.it/it/documenti_pubblici/doc-pre-di-indirizzo-della-variante-generale-al-p-r-g)
  - `elab_b01_zoning_prg_vigente.pdf` — **zoning attuale da scaricare per il case study** (TODO)
  - `elab_00_dp01_documento_obiettivi.pdf` — obiettivi variante
  - `elab_00_dp03_schema-preliminare.pdf` — schema preliminare nuova zonizzazione

### 1.2 Obiettivi della variante (rilevanti per il caso)
La variante dichiara esplicitamente di voler:
- **"ridefinire le previsioni urbanistiche del vigente PRG in relazione alle aree turistico-ricettive"**
- Rilanciare la vocazione turistica in chiave sostenibile
- Tutelare il patrimonio artistico/culturale/paesaggistico
Questo è **critico**: significa che la zonizzazione ricettiva è oggetto attivo di revisione → rischio regolatorio su scenario cambio d'uso.
Fonte: [Comune di Gaeta — Piano Regolatore Generale: Ecco le linee guida](https://www.comune.gaeta.lt.it/News/Piano-Regolatore-Generale-Ecco-le-linee-guida) (URL in news section)

### 1.3 Zonizzazione Serapo — DA VERIFICARE SUL DOCUMENTO UFFICIALE
**NOT FOUND in open web** la classificazione puntuale del lotto (tipicamente PRG 1973 classifica zone F o C per aree ricettive costiere). Richiede:
- Download `elab_b01_zoning_prg_vigente.pdf` dal sito comunale
- OCR + estrazione zona con coordinate del lotto cliente
- Verifica NTA (Norme Tecniche di Attuazione) del PRG 1973

**Ipotesi di lavoro** (da confermare): zona F attrezzature turistico-ricettive con indice fondiario ~1-2 mc/mq, altezza max ~10-13 m, in fascia costiera sottoposta a vincolo paesaggistico.

---

## 2. Vincoli paesaggistici

### 2.1 PTPR Lazio (Piano Territoriale Paesistico Regionale)
- **PTPR approvato** con D.C.R. Lazio n. 5 del 21 aprile 2021, pubblicato sul BURL n. 56 del 10 giugno 2021.
  Fonti:
  - [Regione Lazio — PTPR](https://www.regione.lazio.it/cittadini/urbanistica/pianificazione-paesaggistica/ptpr)
  - [Norme tecniche PTPR 2022 (PDF)](https://www.regione.lazio.it/sites/default/files/2023-05/b-Norme_228-670-2022.pdf)
- **Gaeta è classificata in Ambito n° 14 "Cassino, Gaeta, Ponza"** del PTPR.
  Fonte: [Urbismap — PTPR Lazio](https://www.urbismap.com/piano/piano-paesaggistico-territoriale-regionale-lazio)

### 2.2 Vincoli operanti sul lotto Serapo (aspettati)
Essendo immobile in fascia costiera tirrenica:
- **Vincolo ex lege art. 142 D.Lgs. 42/2004** — fascia costiera 300 m dalla battigia (lett. a)
- **Possibile vincolo dichiarativo ex artt. 136-138** se l'area è in ambito di notevole interesse pubblico (tipico per Gaeta antica e coste)
- **PTPR — sistema del paesaggio costiero** con NTA specifiche di conservazione/tutela
- Il vincolo NON si applica alle zone A e B urbanistiche come al 6 settembre 1985 (art. 142 c. 2) — **da verificare se Serapo era già zona B alla data**

**NOT FOUND precisamente**: numero e data del decreto ministeriale/regionale di apposizione del vincolo sul perimetro di Gaeta-Serapo. Richiede consultazione SITAP (Sistema Informativo Territoriale Ambientale e Paesaggistico) o soprintendenza Latina.
Fonte tool: [Urbismap SITAP](https://www.urbismap.com/piano/sitap)

---

## 3. Normativa regionale cambio d'uso ricettivo → residenziale

### 3.1 L.R. Lazio 7/2017 "Rigenerazione urbana"
- **Legge Regionale 18 luglio 2017, n. 7** — "Disposizioni per la rigenerazione urbana e per il recupero edilizio"
  Fonte: [Consiglio Regionale Lazio — LR 7/2017 testo coordinato vigente](https://www.consiglio.regione.lazio.it/consiglio-regionale/?vw=leggiregionalidettaglio&id=9313&sv=vigente)
- **Art. 4** disciplina il mutamento di destinazione d'uso. Le destinazioni dichiarate compatibili/complementari includono:
  residenziale / turistico-ricettivo / direzionale / servizi / commerciale vicinato
  → il passaggio **ricettivo ↔ residenziale è intra-funzionale** nel quadro regionale.
- I comuni possono deliberare norme specifiche per cambi d'uso fino a **15.000 mq** (ampiamente superiore ai 900 mq del caso).
  Fonte: [Pisellli & Partners — Legge urbanistica Regione Lazio](https://www.piselliandpartners.com/news-di-settore/urbanistica-edilizia-real-estate-e-procedimenti-espropriativi/legge-urbanistica-della-regione-lazio-destinazione-duso-e-rigenerazione/)

### 3.2 L.R. Lazio 12/2025 — modifica sostanziale (nuovo art. 4)
- **L.R. 30 luglio 2025, n. 12** ha **integralmente riscritto l'art. 4 della L.R. 7/2017** sul mutamento di destinazione d'uso.
  Fonti:
  - [Consiglio Regionale Lazio — LR 12/2025 testo coordinato](https://www.consiglio.regione.lazio.it/consiglio-regionale/?vw=leggiregionalidettaglio&id=9514&sv=vigente)
  - [Legal Team — LR 12/2025 art. 4 LR 7/2017](https://legal-team.it/lazio-12-2025-art-4-lr-7-2017-rigenerazione-urbana-cambio-uso/)
  - [Carteinregola — modifiche LR 7/2017](https://www.carteinregola.it/urbanistica/norme-urbanistiche-ed-edilizie/legge-urbanistica-regione-lazio/legge-regione-lazio-le-modifiche-alle-legge-7-2017-rigenerazione-urbana/)
- ATTENZIONE: **Corte Costituzionale, sent. n. 51 del 18/04/2025** ha dichiarato **incostituzionale** l'art. 4 c. 4 della L.R. 7/2017 nella versione transitoria. Questo è il motivo della riscrittura 2025.
  Fonte: [Build News — Consulta boccia legge Lazio](https://www.buildnews.it/articolo/trasformazione-edilizia-cambio-destinazione-duso-consulta-boccia-legge-lazio)
- **Implicazione operativa**: il framework è in assestamento. Nuovi progetti di cambio d'uso devono seguire il testo aggiornato 2025 e verificare lo strumento urbanistico comunale.

### 3.3 L.R. Lazio 8/2022 — sistema turistico + svincolo alberghiero
- **Legge Regionale 24 maggio 2022, n. 8** — ha modificato la L.R. 13/2007 su organizzazione sistema turistico.
  Fonte: [Consiglio Regionale Lazio — LR 8/2022](https://www.consiglio.regione.lazio.it/?vw=leggiregionalidettaglio&id=9438&sv=vigente)
- Disciplina specificamente **"la rimozione del vincolo di destinazione alberghiera in caso di interventi edilizi sugli esercizi alberghieri"** e condhotel.
  Fonte: [Legislazione Tecnica — LR Lazio 8/2022](https://www.legislazionetecnica.it/8761075/normativa-edilizia-appalti-professioni-tecniche-sicurezza-ambiente/l-r-lazio-24-05-2022-n-8/sistema-turistico)
- **Rilevanza per il caso**: se l'immobile Serapo ha vincolo di destinazione alberghiera (tipico per strutture finanziate con contributi regionali o istituite in zona F alberghiera), lo svincolo è possibile ma deve seguire la procedura ex L.R. 8/2022 → verifica preliminare obbligatoria.

### 3.4 Piano Casa Lazio (L.R. 21/2009)
- **L.R. 11 agosto 2009, n. 21** — "Misure straordinarie per il settore edilizio" (Piano Casa).
  Fonte: [Consiglio Regionale Lazio — LR 21/2009](https://www.consiglio.regione.lazio.it/consiglio-regionale/?vw=leggiregionalidettaglio&id=9172&sv=vigente)
- Riscritto da L.R. 10/2014 (in vigore dal 12/11/2014).
  Fonte: [UNINDUSTRIA — Modifiche Piano Casa LR 10/2014](https://www.un-industria.it/canale/edile/notizia/33191/modifiche-al-piano-casa-regionale-del-lazio-lr-n/)
- **Scaduto il 1° giugno 2017**, ma i principi sono confluiti nelle norme strutturali (L.R. 7/2017). Non direttamente applicabile al 2026.
  Fonte: [Mansarda.it — Piano Casa Lazio 2026](https://www.mansarda.it/leggi-e-regolamenti/piano-casa-lazio-cosa-ce-da-sapere-nel-2026/)

### 3.5 Quadro nazionale — Salva Casa 2024
- Il DL 69/2024 "Salva Casa" (convertito L. 105/2024) ha ampliato a livello nazionale i casi di cambio d'uso "libero" tra categorie compatibili nelle zone A, B, C del DM 1444/68.
  Fonte: [BibLus — Cambio destinazione d'uso TU e Salva Casa](https://biblus.acca.it/cambio-destinazione-duso/)

### 3.6 Sintesi executive
> Il cambio d'uso ricettivo → residenziale in Lazio è **tecnicamente possibile** ex L.R. 7/2017 art. 4 (come riscritto dalla L.R. 12/2025), ma nel caso Gaeta-Serapo va verificato:
> 1. Classificazione urbanistica del lotto nel PRG 1973 (zona F/alberghiera → serve possibilità di mutamento)
> 2. Presenza di eventuale vincolo ex L.R. 8/2022 che richiede procedura di svincolo alberghiero
> 3. Compatibilità con PTPR ambito 14 (autorizzazione paesaggistica ex art. 146 D.Lgs. 42/2004 quasi certa)
> 4. Non blocco da parte della variante generale PRG in corso (misure di salvaguardia)

---

## 4. Dati OMI — Gaeta e Serapo

### 4.1 OMI Agenzia Entrate — fonte ufficiale
- Portale quotazioni: [Agenzia Entrate — Quotazioni immobiliari OMI](https://www.agenziaentrate.gov.it/portale/schede/fabbricatiterreni/omi/banche-dati/quotazioni-immobiliari)
- Endpoint di ricerca pubblico: [Banca dati quotazioni OMI — ricerca](https://www1.agenziaentrate.gov.it/servizi/Consultazione/ricerca.htm)
- Frequenza: semestrale
- **Guida consultazione**: [Guida Consultazione Quotazioni OMI (PDF)](https://www.agenziaentrate.gov.it/portale/documents/20143/264034/guida_cons_quotOMI_Guida+alla+Consultazione+delle+Quotazioni+OMI.pdf/0d0cab67-634d-e2c0-b215-2b39a09af27e)

### 4.2 Valori OMI Gaeta (aggregati da mercato-immobiliare.info)
Fonte: [mercato-immobiliare.info — Gaeta](https://www.mercato-immobiliare.info/lazio/latina/gaeta.html)

| Zona | Descrizione | Residenziale €/mq (min-max) | Locazione €/mq/mese |
|---|---|---|---|
| B3 | **ZONA CENTRALE — Via Marina di Serapo, Via Fontania, Via Garibaldi** | fino a **~2.655–2.780** | fino a ~12.5 |
| R1 | Zona agricola | ~1.130 | ~5.3 |
| range complessivo città | 7 zone OMI | **1.130 — 2.780** | 5.3 — 12.5 |

**Nota critica**: la zona B3 è quella che include Via Marina di Serapo (nome confermato). OMI è "semaforico" ufficiale ma sottovalutato rispetto a listini.

### 4.3 Listini / asking prices (mercato attivo)
Fonte: [Immobiliare.it — Quotazioni Gaeta](https://www.immobiliare.it/en/mercato-immobiliare/lazio/gaeta/)

| Metrica | Valore (set. 2025 - feb. 2025 picco) |
|---|---|
| Prezzo medio richiesto Gaeta città | **€3.318/mq** (set. 2025) |
| Picco 2 anni | €3.340/mq (feb. 2025) |
| **Serapo — medio richiesto** | **~€3.930/mq** |
| Serapo — range tipico | **€2.465 — €4.500/mq** |
| **Serapo — vista mare** | **~€4.411/mq** |

**Delta Serapo vs città**: +16% rispetto alla media comunale (una delle zone più pricey di Gaeta).

### 4.4 Gap: valori ricettivo/alberghiero
**NOT FOUND** nei dati open aggregati. OMI pubblica anche la categoria "alberghi e pensioni" per codice zona — richiede query diretta sull'endpoint OMI con filtro tipologia. Ipotesi plausibile: €1.200-1.800/mq (tipicamente 30-50% sotto residenziale di pari zona, coerente col mercato italiano 2025).

---

## 5. Costi di costruzione — Prezzario Lazio 2025

### 5.1 Fonte ufficiale
- **Prezzario Regione Lazio — edizione 2023-2024-2025-2026**, approvato con Risoluzione n. 101 del 14 aprile 2023.
  Fonti:
  - [Assimpredil ANCE — Prezzario Lazio 2025](https://portale.assimpredilance.it/articoli/pubblicato-il-prezzario-regionale-per-le-opere-pubbliche-2025-e-attivata-la-piattaforma-digitale-per-la-consultazione)
  - [ACCA — Prezzario Regione Lazio](https://www.acca.it/prezzario-regione-lazio)
  - [TeamSystem Construction — Prezzario Lazio](https://www.teamsystem.com/construction/prezzari/regione-lazio/)
- Piattaforma digitale attiva per consultazione gratuita.

### 5.2 Benchmark operativi (da literatura di settore, da validare col prezzario)
| Intervento | €/mq (stima range, Lazio 2025) |
|---|---|
| Ristrutturazione leggera residenziale (impianti + finiture) | 600 — 1.000 |
| Ristrutturazione pesante con redistribuzione | 1.000 — 1.600 |
| Demolizione + ricostruzione residenziale nuova | 1.400 — 2.200 |
| Conversione ricettivo → residenziale (redistribuzione bagni, cucine, scale, impianti) | **1.100 — 1.700** |
| Oneri concessori + progettazione + DL + IVA | +18-25% sul costo opere |

**NOT FOUND precisi**: voci specifiche prezzario Lazio. Richiede download del prezzario 2026 (attualmente piattaforma regionale) e matching voci per intervento di conversione. TODO per il report definitivo.

---

## 6. Gaps aperti (da chiudere prima del report finale)

| # | Gap | Come risolvere | Ore |
|---|---|---|---|
| 1 | Classificazione PRG 1973 del lotto esatto (zona + indici) | Download `elab_b01_zoning_prg_vigente.pdf` + NTA PRG + visura catastale | 3 |
| 2 | Vincolo paesaggistico specifico decreto | Query SITAP + Soprintendenza Latina | 2 |
| 3 | Eventuale vincolo alberghiero esistente | Verifica atto costitutivo immobile + scheda SUAP | 2 |
| 4 | OMI ricettivo/alberghiero Gaeta B3 | Query diretta OMI con categoria tipologica | 1 |
| 5 | Voci prezzario Lazio 2025 specifiche per conversione | Download prezzario + estrazione voci | 3 |
| 6 | Comparables reali ultimi 12 mesi Serapo (vendite chiuse) | Query RGM (Registro Generale Mercato) Agenzia Entrate o richiesta notarile | 4 |
| 7 | Misure di salvaguardia variante PRG in corso | Delibera C.C. adozione + art. di legge | 1 |
| 8 | Data-room cliente (catasto, APE, perizia, atto) | Richiesta a Danny + cliente finale | — |

Totale per completare: **~16 ore di lavoro analista** + tempo attesa risposte enti.
