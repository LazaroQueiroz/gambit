#!/usr/bin/env python3
"""A minimal UCI engine for tests: it reports a fixed score and the first legal moves."""

import sys

import chess

board = chess.Board()
multipv = 1
for raw in sys.stdin:
    parts = raw.split()
    if not parts:
        continue
    if parts[0] == "uci":
        print("id name FakeFish\noption name Threads type spin default 1 min 1 max 8\n"
              "option name Hash type spin default 16 min 1 max 1024\n"
              "option name MultiPV type spin default 1 min 1 max 8\nuciok", flush=True)
    elif parts[0] == "isready":
        print("readyok", flush=True)
    elif parts[0] == "setoption" and parts[2] == "MultiPV":
        multipv = int(parts[4])
    elif parts[0] == "position":
        board = chess.Board()
        if "moves" in parts:
            for uci in parts[parts.index("moves") + 1:]:
                board.push_uci(uci)
    elif parts[0] == "go":
        legal = list(board.legal_moves)
        for i, move in enumerate(legal[:multipv], 1):
            print(f"info depth 5 multipv {i} score cp {30 - i * 10} pv {move.uci()}", flush=True)
        print(f"bestmove {legal[0].uci()}", flush=True)
    elif parts[0] == "quit":
        break
