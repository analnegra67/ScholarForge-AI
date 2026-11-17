"""End-to-end scenario: the 'Mathematical Foundations of Deep Learning' test book.

A scripted host stands in for the AI client so the whole pipeline is reproducible
and cheap: the answers it gives are real authored content stored under
``fixtures/``; everything else (validation, verification, rendering, preflight,
recovery) is the production code path.
"""
