# End-to-end scenario: PASS

- Duration: 315.0 s
- Sources: recorded API responses
- Project: `<project>`

| Step | Result |
|:--|:--|
| create_project | ok |
| interrupt_mid_session | ok |
| resume_after_interruption | ok |
| authoring_loop | ok |
| fabricated_reference_rejected | ok |
| mismatched_reference_detected_and_corrected | ok |
| sources_verified | ok |
| math_checks | ok |
| claims_classified | ok |
| consistency | ok |
| figures_rendered | ok |
| blocks_written | ok |
| build_html | ok |
| build_epub | ok |
| build_pdf | ok |
| build_print_pdf | ok |
| print_preflight | ok |
| export_latex | ok |
| export_quarto_source | ok |
| rebuild_from_sources_without_model | ok |
| verification_report | ok |

## Details

- **create_project**: {'project_id': 'prj_01M7NHYBZT8BM04JBQP8MQVWBY', 'path': '<project>', 'topic': 'Mathematical Foundations of Deep Learning', 'required_topics': ['calculus', 'linear algebra', 'optimization', 'automatic differentiation', 'neural networks'], 'profile': 'machine-learning'}
- **interrupt_mid_session**: {'leased_task': 'draft.block'}
- **resume_after_interruption**: {'released': 1, 'phase': 'drafting'}
- **authoring_loop**: {'final_status': 'ready_to_build', 'tasks': 23, 'revisions': 1}
- **fabricated_reference_rejected**: {'status': 'rejected', 'reason': 'identifier does not resolve in any service (crossref:10.9999/sf-fabricated.2019.00123, openalex:10.9999/sf-fabricated.2019.00123, semantic_scholar:10.9999/sf-fabricated.2019.00123); the reference is probably fabricated or mistyped'}
- **mismatched_reference_detected_and_corrected**: {'status': 'verified', 'doi': '10.1038/nature16961'}
- **sources_verified**: {'verified': 12, 'total': 13, 'not_verified': {'doe2019': 'rejected'}}
- **math_checks**: {'results': {'pass:symbolic': 17, 'pass:numeric': 3}}
- **claims_classified**: {'claims': {'unverified': 4, 'verified': 7, 'supported': 13, 'contested': 1}}
- **consistency**: {'findings': {'NOTATION_MULTIPLE_MEANINGS': 1}}
- **figures_rendered**: {'figures': {'fig-sigmoid': 'approved', 'fig-gd-paths': 'approved', 'fig-comp-graph': 'approved'}}
- **blocks_written**: {'statuses': {'Derivatives and the chain rule': 'verified', 'Vectors, matrices and gradients': 'verified', 'Gradient descent and convexity': 'verified', 'Forward and reverse mode': 'verified', 'Feedforward networks and backpropagation': 'verified', 'Universal approximation': 'verified'}, 'words': 1610}
- **build_html**: {'path': '<project>\\exports\\html\\index.html', 'preflight': {'ok': True, 'errors': [], 'warnings': [], 'info': {'pages': 10, 'broken_links': 0, 'math': True}}}
- **build_epub**: {'path': '<project>\\exports\\epub\\book.epub', 'preflight': {'ok': True, 'errors': [], 'warnings': [], 'epubcheck': {'fatal': 0, 'errors': 0, 'warnings': 0, 'usage': 0, 'checker': '5.4.0'}}}
- **build_pdf**: {'path': '<project>\\exports\\pdf\\book.pdf', 'pages': 17, 'preflight': {'ok': True, 'errors': [], 'warnings': ['document actions present'], 'pdfx': None}}
- **build_print_pdf**: {'path': '<project>\\exports\\print\\interior.pdf', 'pages': 25, 'pdfx': True, 'error': None}
- **print_preflight**: {'interior': {'ok': True, 'errors': [], 'warnings': [], 'pdfx': "passes ScholarForge's PDF/X-4 checks (not a certified validation)"}, 'cover': {'ok': True, 'errors': [], 'warnings': [], 'pdfx': "passes ScholarForge's PDF/X-4 checks (not a certified validation)"}}
- **export_latex**: {'path': '<project>\\exports\\latex\\book.tex'}
- **export_quarto_source**: {'path': '<project>\\exports\\quarto-source'}
- **rebuild_from_sources_without_model**: {}
- **verification_report**: {'report': '<project>\\reports\\verification-report.md', 'claims': {'verified': 7, 'supported': 13, 'contested': 1, 'unverified': 4, 'requires_expert_review': 0}, 'open_issues': 0}
