# Developer Tools

Lokale Helfer für die Arbeit am racetag-Repo (nicht Teil der App).

## `rtsearch` — Repo-Suche mit Ollama

Eine zsh-Funktion, die das Repo mit ripgrep durchsucht und die Fundstellen
lokal von einem Ollama-Modell (`qwen2.5:7b`) erklären lässt. Läuft komplett
offline, kein API-Key nötig.

### Voraussetzungen

- [Ollama](https://ollama.com) installiert (`brew install ollama`)
- Modell vorhanden: `ollama pull qwen2.5:7b`
- [ripgrep](https://github.com/BurntSushi/ripgrep) (`brew install ripgrep`)

### Installation

Die Funktion ist in `~/.zshrc` definiert (nicht im Repo versioniert, da
maschinenlokal). Definition:

```zsh
rtsearch() {
  emulate -L zsh
  local repo="/Users/jan/Documents/git/racetag"
  local model="qwen2.5:7b"
  if [[ -z "$1" ]]; then
    print -u2 "Usage: rtsearch <suchbegriff> [frage…]"
    return 1
  fi
  local pattern="$1"; shift
  local question="$*"
  [[ -z "$question" ]] && question="Erkläre knapp, wo und wofür '$pattern' im Code verwendet wird."

  local hits
  hits=$(rg --line-number --with-filename --context 2 --max-columns 200 --color never -- "$pattern" "$repo" 2>/dev/null | head -300)
  if [[ -z "$hits" ]]; then
    print -u2 "rtsearch: keine Treffer für '$pattern' in $repo"
    return 1
  fi

  ollama run "$model" "Du bist ein Code-Assistent für das Repository 'racetag'.
Unten stehen ripgrep-Treffer (Datei:Zeile + Kontext) zum Suchbegriff '$pattern'.
Beantworte auf Deutsch, knapp und präzise: $question
Beziehe dich auf konkrete Dateien und Zeilennummern.

=== Treffer ===
$hits"
}
```

### Verwendung

```bash
# Suchbegriff erklären lassen
rtsearch "csv"

# Mit eigener Frage
rtsearch "end_race" "Wie läuft das Beenden eines Rennens ab?"
```

- **Arg 1** = Suchbegriff (ripgrep-Pattern)
- **Rest** = optionale Frage in natürlicher Sprache; ohne Frage wird der Begriff
  generisch erklärt.
- Findet ripgrep nichts, bricht die Funktion ab, bevor das Modell startet.
- Treffer werden auf 300 Zeilen begrenzt, damit der Modell-Kontext nicht
  überläuft.
