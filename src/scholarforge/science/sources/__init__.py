"""Scholarly source verification against open bibliographic services.

Services: Crossref (DOIs), OpenAlex (works, retractions, abstracts), arXiv
(preprints), Semantic Scholar (fallback lookup) and Open Library (books by ISBN).
All responses are cached on disk so that verification is reproducible and works
offline once fetched.
"""
