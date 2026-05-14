# 🎮 poke-player

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

> **AI-powered Pokémon gameplay agent** with headless emulation, REST API, and live dashboard.

Control Pokémon games (Game Boy / GBA) programmatically via HTTP API, read live game state from emulator RAM, and watch the AI play through a real-time web dashboard.

---

## ✨ Features

- 🕹️ **Headless Emulation** — PyBoy (GB/GBC) and PyGBA (GBA) backends
- 🧠 **Live Memory Reading** — Extract player data, party Pokémon, battle state, bag items, map position, and story flags directly from RAM
- 🌐 **REST API + WebSocket** — Control the emulator via HTTP and receive real-time state updates
- 📊 **Live Dashboard** — Beautiful cyberpunk-themed web UI with game screen, team stats, battle info, and AI action log
- 🔍 **A* Pathfinding** — Grid-based navigation with collision map support
- 💾 **Save States** — Named save/load slots for quick experimentation
- 📸 **Screenshots** — Capture PNG frames on demand

---

## 🚀 Quick Start

### Installation

```bash
# Basic install (PyBoy for GB/GBC)
pip install poke-player

# With GBA support
pip install "poke-player[pyboy,pygba]"

# With dashboard
pip install "poke-player[dashboard]"

# Everything
pip install "poke-player[all]"
```

### Start the Server

```bash
poke-player serve --rom path/to/red.gb --port 8765
```

### View the Dashboard

Open http://localhost:8765/dashboard in your browser.

---

## 🛠️ CLI Usage

```bash
# Start server
poke-player serve --rom red.gb --data-dir ~/.poke-player

# Show ROM info
poke-player info --rom red.gb

# Get current game state
poke-player state

# Press buttons
poke-player action a
poke-player action "up,up,a,b"

# Save / load state
poke-player save before_gym
poke-player load before_gym
poke-player saves

# Screenshot
poke-player screenshot --out frame.png

# Minimap
poke-player minimap

# Stop server
poke-player stop
```

---

## 🔌 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Server health check |
| `GET` | `/state` | Full game state JSON |
| `GET` | `/state/summary` | Human-readable state summary |
| `POST` | `/action` | Press buttons (`{"actions": ["a"]}`) |
| `POST` | `/save` | Save state (`{"name": "slot1"}`) |
| `POST` | `/load` | Load state (`{"name": "slot1"}`) |
| `GET` | `/saves` | List save slots |
| `GET` | `/screenshot` | PNG screenshot (base64 or raw) |
| `GET` | `/minimap` | Explored area minimap |
| `GET` | `/info` | ROM metadata |
| `WS` | `/ws` | WebSocket for live updates |

---

## 🏗️ Architecture

```
┌─────────────┐     HTTP/WS      ┌──────────────┐     ┌─────────────┐
│   Dashboard │ ◄──────────────► │  FastAPI     │ ◄──►│   PyBoy     │
│  (Browser)  │                  │   Server     │     │  / PyGBA    │
└─────────────┘                  └──────────────┘     └─────────────┘
                                        │
                                        ▼
                              ┌──────────────────┐
                              │  Memory Reader   │
                              │  (Red/FireRed)   │
                              └──────────────────┘
```

---

## 🧪 Development

```bash
# Clone
git clone https://github.com/KevRojo/poke-player.git
cd poke-player

# Install in editable mode
pip install -e ".[all,dev]"

# Run tests
pytest

# Run with coverage
pytest --cov=poke_player

# Lint
ruff check .
```

---

## 🗺️ Supported Games

| Game | Platform | Status | Reader |
|------|----------|--------|--------|
| Pokémon Red/Blue | Game Boy | ✅ Complete | `memory/red.py` |
| Pokémon FireRed | GBA | 🚧 Stub | `memory/firered.py` |

---

## 📁 Project Structure

```
poke_player/
├── cli.py           # Command-line interface
├── server.py        # FastAPI HTTP/WebSocket server
├── emulator.py      # Emulator wrapper (PyBoy / PyGBA)
├── pathfinding.py   # A* tile navigation
├── memory/
│   ├── reader.py    # Abstract memory reader
│   ├── red.py       # Pokémon Red/Blue reader
│   └── firered.py   # FireRed reader (Phase 2)
├── state/
│   └── builder.py   # Game state assembly
└── dashboard/
    └── static/      # Web dashboard (HTML/CSS/JS)
```

---

## 🤝 Contributing

Contributions welcome! Please open an issue or PR.

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/amazing`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing`)
5. Open a Pull Request

---

## 📜 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgments

- Memory addresses from [pret/pokered](https://github.com/pret/pokered)
- Emulator backends: [PyBoy](https://github.com/Baekalfen/PyBoy), [PyGBA](https://github.com/mattbruv/PyGBA)

---

<p align="center">
  <i>Made with 💜 by KevRojo</i>
</p>
