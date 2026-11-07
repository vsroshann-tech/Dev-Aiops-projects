# AI-Assisted Dockerfile Generator: Project Summary

**Author:** Roshan V S
**Date:** 21 October 2026


---

## 1. Objective

Build and test a small tool that uses an LLM to generate best-practice Dockerfiles for any programming language, and compare two approaches:

1. **Local LLM** using Ollama (runs offline on my machine)
2. **Hosted LLM** using Google Gemini (cloud API)

---

## 2. Environment Setup

**Platform:** Ubuntu (WSL) on Windows, Python 3.14.4.

### Ollama

| Command | What it does |
|---|---|
| `ollama serve` | Starts the Ollama server on port 11434. It failed with `address already in use`, which meant Ollama was already running in the background, so no second instance was needed. |
| `ollama run` | Runs a model interactively. It needs a model name, so running it without one gave an error. |
| `ollama pull llama3.2:1b` | Downloads the 1B-parameter Llama 3.2 model (about 1.3 GB) from the Ollama registry. |
| `ollama -h` | Shows help and the list of available subcommands. (`olama -h` was a typo.) |
| `ollama run llama3.2` | Starts a chat with the model in the terminal. I used it to test with the prompt "give a dockerfile". |
| `ollama list` | Lists downloaded models. This showed I had `llama3.2:1b` and `llama3.2:latest`, not `llama3.1:8b`. |

### Python virtual environment

| Command | What it does |
|---|---|
| `python3 -m venv venv` | Creates an isolated Python environment named `venv`. It failed because the `venv` package was missing. |
| `apt install python3.14-venv` | Installs the missing package. It failed without root rights (permission denied on the dpkg lock). |
| `sudo apt install python3.14-venv` | Same command with admin rights. It then reported that dpkg was interrupted. |
| `sudo dpkg --configure -a` | Finishes any half-completed package installs and repairs the package manager. |
| `sudo apt update` | Refreshes the package lists from the Ubuntu repositories. |
| `python3 --version` | Confirmed the Python version (3.14.4). |
| `rm -rf ~/venv` | Deleted the broken venv folder before recreating it. |
| `source ~/venv/bin/activate` | Activates the environment, so `python` and `pip` point to the venv. The prompt shows `(venv)`. |
| `python --version`, `pip --version`, `which python` | Verified that the venv's Python and pip were the ones in use. |

I also tried `.\venv\Scripts\activate`, which is the Windows path and does not work on Linux. The Linux equivalent is `venv/bin/activate`.

---

## 3. Part 1: Local LLM with Ollama

### Commands used

| Command | What it does |
|---|---|
| `git clone <url>` | Copies the repo to my machine. The first attempt failed because I used a `/tree/main/day-3` browser URL. The correct one is the plain repo URL ending in `.git`. |
| `cd`, `ls -la`, `ll`, `pwd` | Navigate folders, list files (including hidden ones, with details) and print the current path. |
| `find ~/ai-assisted-devops -name "requirements*.txt" -type f` | Searched the repo for `requirements.txt`, which showed it lives in `local-llms-ollama/`, not in `day-3/`. |
| `pip3 install -r requirements.txt` | Installs the Python packages listed in the file (here, the `ollama` client library). |
| `vim generate_dockerfile.py` | Edited the script. |
| `cat generate_dockerfile.py` | Printed the file to check its contents. |
| `python3 generate_dockerfile.py` | Ran the generator. It asks for a language and prints a Dockerfile. |

### How it works

The script sends a prompt to the local model through `ollama.chat()` and prints the reply.

### Issue and fix

- The script was hardcoded to `llama3.1:8b`, which I had not downloaded, so it failed with `404 model not found`.
- I changed it to `llama3.2:1b`, which I did have.
- I also added "Multi stage docker build" to the prompt.

### Results

| Language | Quality of output |
|---|---|
| Groovy | Inaccurate (wrong base images and commands) |
| Python | Partly correct, but installed dependencies before copying `requirements.txt` |
| Ruby on Rails | Poor (used `pip` for a Ruby project, wrong `CMD`) |

**Takeaway:** The small 1B model is fast and private, but it often produces incorrect Dockerfiles. Always review the output.

---

## 4. Part 2: Hosted LLM with Google Gemini

### Commands used

| Command | What it does |
|---|---|
| `nano generate_dockerfile_gemini.py` | Edited the script with the nano editor. |
| `pip3 install google.generativeai` | Installed the Gemini SDK. This fixed `No module named 'google'`. |
| `python -m pip install -U google-genai` | Installed the newer, supported Gemini SDK (`-U` means upgrade). The old package had reached end of support. |
| `read -s -p "Enter your new Gemini API key: " GEMINI_API_KEY` | Prompts for the key without showing it on screen (`-s` is silent), and stores it in a shell variable. |
| `export GEMINI_API_KEY` | Makes the variable available to programs I launch from this terminal, such as the Python script. |
| `python generate_dockerfile_gemini.py` | Ran the Gemini version of the generator. |

### Issues and fixes

1. **Missing module:** fixed by installing the package.
2. **Deprecated SDK:** `google.generativeai` is no longer maintained, so I moved to `google-genai`.
3. **Model not found (404):** `gemini-1.5-pro` is no longer available, so I switched to a supported model.
4. **API key security:** The original script had the key hardcoded. I created a new key and loaded it from the `GEMINI_API_KEY` environment variable instead, so it is not stored in the source file.

### Result

For `nodejs`, Gemini produced a correct, production-quality **multi-stage Dockerfile**:

- Lightweight pinned base image (`node:20-alpine`)
- Dependencies installed in a separate stage with `npm ci --omit=dev`
- Cache cleanup to reduce image size
- Runs as the non-root `node` user
- Sets `NODE_ENV=production` and exposes the port

---

## 5. Comparison

| | Ollama (llama3.2:1b) | Gemini (hosted) |
|---|---|---|
| Cost | Free | API-based (free tier available) |
| Privacy | Fully local | Prompts sent to the cloud |
| Speed | Depends on hardware | Fast |
| Accuracy | Low, often wrong | High, follows best practices |
| Setup | About 1.3 GB model download | API key needed |

---

## 6. Key Learnings

- How to run local models with Ollama and call them from Python.
- How to troubleshoot environment problems: dpkg locks, missing venv package, port conflicts, wrong model names and deprecated SDKs.
- Model size matters: small local models are not reliable for infrastructure code.
- Never hardcode API keys. Use environment variables, and rotate any key that has been exposed.
- Prompt wording (for example, asking for multi-stage builds) changes the output noticeably.

## 7. Possible Next Steps

- Try a larger local model (such as `llama3.1:8b`) and compare quality.
- Build and run the generated Dockerfiles to validate them automatically.
- Keep API keys in a `.env` file or a secrets manager.
