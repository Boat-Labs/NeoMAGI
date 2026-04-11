# NeoMAGI

[English](README.md) | [中文](README_ch.md)

NeoMAGI ist ein Open-Source-Projekt fuer einen personal agent.

Die Produktidee ist einfach: ein Agent, der ueber laengere Zeit nuetzliche Erinnerung behalten kann, die Informationsinteressen des Nutzers vertritt und sich schrittweise von gehosteten Modell-APIs zu lokaleren und besser kontrollierbaren Modell-Stacks bewegen kann.

## Produktpositionierung

NeoMAGI soll keine generische Chatbot-Huelle sein.

Die angestrebte Richtung ist eine langfristige partnerartige AI mit folgenden Eigenschaften:
- nuetzlichen Kontext ueber Zeit behalten
- im Interesse des Nutzers handeln statt im Interesse einer Plattform
- Faehigkeiten kontrolliert und auditierbar erweitern
- einen realistischen Migrationspfad von kommerziellen APIs zu lokalen Modellen offenhalten

## Prinzipien

- Gruendlich denken, einfach umsetzen.
- Zuerst den kleinsten brauchbaren geschlossenen Kreislauf bauen.
- Unnoetige Abstraktion und Abhaengigkeiten vermeiden.
- Governance, Rollback und Scope-Grenzen als Produktmerkmale behandeln, nicht nur als Engineering-Details.

## Was bereits gebaut ist

- **Mehrkanal-Interaktion**: WebSocket (WebChat) und Telegram, mit kanalunabhaengigem Dispatch
- **Persistenter Speicher**: PostgreSQL-basierte Hybridsuche (Vektor + Keyword), sitzungsbewusste Scope-Aufoesung, Anti-Drift-Komprimierung
- **Wachstums-Governance**: explizite, verifizierbare, rueckrollbare Evolution — jede Faehigkeitsaenderung wird vorgeschlagen, evaluiert, angewendet und auditiert
- **Skill-Objekte**: eine Laufzeit-Erfahrungsschicht, die wiederverwendbares Aufgabenwissen erfasst, damit der Agent nicht jedes Mal bei null anfaengt
- **Procedure Runtime**: deterministische mehrstufige Ausfuehrung mit Steuerung, Checkpoints und Wiederaufnahme
- **Multi-Agent-Ausfuehrung**: kontrollierte Uebergabe zwischen Agents unter einem einzigen Principal, mit governiertem Kontextaustausch
- **Multi-Provider-Modelle**: OpenAI und Gemini, mit Per-Run-Routing und atomarem Budget-Gating
- **Betriebszuverlaessigkeit**: Startup-Preflight-Checks, Laufzeitdiagnose, strukturiertes Backup und Restore

## Aktueller Stand

Phase 1 (Fundament) ist abgeschlossen: Sitzungskontinuitaet, persistenter Speicher, Modellmigration, Telegram-Kanal, Betriebszuverlaessigkeit und Entwicklungs-Governance ueber 7 Meilensteine.

Phase 2 (explizites Wachstum und verifizierbare Evolution) wird aktiv aufgebaut:
- **P2-M1** (Explizites Wachstum & Builder-Governance): abgeschlossen — Wachstums-Governance-Kern, Skill-Objekte-Runtime, Wrapper Tools, Growth Cases
- **P2-M2** (Procedure Runtime & Multi-Agent-Ausfuehrung): Kern abgeschlossen — Procedure Runtime, Multi-Agent-Handoff, ProcedureSpec-Governance-Adapter
- **P2-M2d** (Memory Source Ledger Prep): naechster Schritt — DB-Append-only-Writer, Dual-Write mit Parity-Checks
- **P2-M3** (Principal & Memory Safety): geplant — WebChat-Authentifizierung, kanonische Benutzeridentitaet, Memory-Sichtbarkeitsrichtlinien, Shared-Space-Sicherheitsskelett

Phase-3-Richtungen (Kandidat, noch nicht aktiv): governierter Self-Evolution-Workflow.

## Tech-Stack

- **Sprache**: Python 3.12+ (async/await)
- **Backend**: FastAPI + WebSocket
- **Speicher**: PostgreSQL 17 + pgvector
- **LLM**: OpenAI SDK, Gemini — Per-Run-Provider-Routing
- **Embedding**: Ollama (bevorzugt) → OpenAI (Fallback)
- **Werkzeuge**: uv, pnpm (Frontend), just, ruff, pytest

## Dokumentation

- Design-Einstieg: `design_docs/index.md`
- Phase-2-Roadmap: `design_docs/phase2/roadmap_milestones_v1.md`
- Phase-2-Architekturindex: `design_docs/phase2/index.md`
- Domaen-Glossar: `design_docs/GLOSSARY.md`
- Modulgrenzen: `design_docs/modules.md`
- Laufzeit-Prompt-Modell: `design_docs/system_prompt.md`
- Memory-Architektur: `design_docs/memory_architecture_v2.md`
- Procedure Runtime: `design_docs/procedure_runtime.md`
- Skill-Objekte: `design_docs/skill_objects_runtime.md`
- Phase-1-Archiv: `design_docs/phase1/index.md`
- Repository-Governance: `AGENTS.md`, `CLAUDE.md`, `AGENTTEAMS.md`

## Hinweis

Das Projekt entwickelt sich aktiv weiter.

Namen, Grenzen und Implementierungsdetails koennen sich weiter aendern, waehrend die Produktrichtung schaerfer wird und mehr Systemteile durch echte Nutzung validiert werden.
