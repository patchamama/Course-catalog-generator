"""
ai_rag_server.py — RAG (Retrieval-Augmented Generation) Server Module
======================================================================
Planned standalone module for the RAG pipeline. Currently the RAG logic
lives inside catalog_server.py (/api/ask endpoint with SSE streaming).

Future refactor goal:
  - Extract RAG pipeline from catalog_server.py into this module
  - Support pluggable retrievers (Elasticsearch kNN, BM25, hybrid RRF)
  - Support pluggable LLMs (Ollama, OpenAI-compatible APIs, HuggingFace)
  - Expose as a standalone Flask Blueprint or FastAPI router
  - Enable independent testing and benchmarking of the RAG pipeline

Current RAG capabilities (in catalog_server.py):
  POST /api/ask        — SSE streaming RAG answer with source citations
  GET  /api/models     — list available Ollama models
  GET  /api/health     — backend health check

Usage (future):
  from ai_rag_server import rag_blueprint
  app.register_blueprint(rag_blueprint, url_prefix='/api/rag')
"""

# Placeholder — implementation pending
# See catalog_server.py for the current RAG implementation
