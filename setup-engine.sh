#!/bin/sh
# Downloads the official Stockfish 19 release into engine/ and checks that it runs.
set -e
url=https://github.com/official-stockfish/Stockfish/releases/download/sf_19/stockfish-linux-x86-64-universal.tar.gz
sha256=9defc0d4e55d49c65a6d042f3e571a39fcea499ade6dbe741b53b8c65e03611f
mkdir -p "$(dirname "$0")/engine"
cd "$(dirname "$0")/engine"
if [ ! -d stockfish ]; then
    if [ ! -f sf.tar.gz ]; then
        curl -fL -o sf.tar.gz.part "$url"
        mv sf.tar.gz.part sf.tar.gz
    fi
    echo "$sha256  sf.tar.gz" | sha256sum -c -
    tar xzf sf.tar.gz
fi
binary=stockfish/stockfish-linux-x86-64-universal
chmod +x "$binary"
printf 'uci\nquit\n' | "./$binary" | grep -m1 '^id name'
echo "Done. gambit will now use Stockfish."
