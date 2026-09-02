#!/bin/sh
set -eu

sudo apt-get update && export DEBIAN_FRONTEND=noninteractive \
&& sudo apt-get -y install --no-install-recommends ripgrep fzf bubblewrap \
&& sudo apt-get clean \
&& sudo rm -rf /var/lib/apt/lists/*

curl -LsSf https://astral.sh/uv/install.sh | sh

uv sync

npm install -g pyright

curl -LsSf https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh
curl -LsSf https://claude.ai/install.sh | bash