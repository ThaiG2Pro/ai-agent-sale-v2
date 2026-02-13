#!/usr/bin/env python3
"""Simple RAG admin CLI stub: ingest / build-index / search

This is a minimal, dependency-free starting point. Expand to call
your ingestion routines, vector index builder, and pgvector search.
"""
import argparse
import sys


def cmd_ingest(path: str) -> int:
    print(f"[rag_admin] ingest: would ingest files from {path}")
    # TODO: call ingestion pipeline (PDF parsing, chunking, metadata)
    return 0


def cmd_build_index() -> int:
    print("[rag_admin] build-index: would (re)build vector index and FTS tables")
    # TODO: connect to DB, upsert vectors, create indexes
    return 0


def cmd_search(query: str, topk: int) -> int:
    print(f"[rag_admin] search: query=\"{query}\" topk={topk}")
    # TODO: perform vector+FTS hybrid search and print results with citations
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="rag_admin", description="RAG admin CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_ingest = sub.add_parser("ingest", help="Ingest documents into RAG pipeline")
    p_ingest.add_argument("path", help="Path to files or folder")

    p_build = sub.add_parser("build-index", help="Build or rebuild vector index")

    p_search = sub.add_parser("search", help="Run a quick hybrid search")
    p_search.add_argument("query", help="Query text")
    p_search.add_argument("--topk", type=int, default=8, help="Top K results")

    args = parser.parse_args(argv)
    if args.cmd == "ingest":
        return cmd_ingest(args.path)
    if args.cmd == "build-index":
        return cmd_build_index()
    if args.cmd == "search":
        return cmd_search(args.query, args.topk)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
