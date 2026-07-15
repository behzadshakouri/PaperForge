# PaperForge

**PaperForge** is an open-source toolkit for automatically extracting
bibliographic metadata from PDF documents and generating high-quality
reference libraries.

Its goal is to simplify the process of building and maintaining research
libraries by combining metadata extraction, online lookup services,
bibliography generation, and citation formatting into a single workflow.

------------------------------------------------------------------------

## Features

### PDF Metadata Extraction

-   Recursive scanning of folders and subfolders
-   Read embedded PDF metadata
-   Extract text from the first pages of PDFs
-   Detect:
    -   DOI
    -   ISBN
    -   arXiv ID
    -   Title
    -   Authors
    -   Publication year

### Automatic Metadata Retrieval

Retrieve authoritative metadata from:

-   Crossref (DOI)
-   Open Library (ISBN)
-   arXiv

### Bibliography Generation

Generate BibTeX entries for:

-   Journal articles
-   Conference papers
-   Books
-   Book chapters
-   PhD dissertations
-   Master's theses
-   Technical reports
-   Miscellaneous documents

### APA Reference Export

Convert BibTeX libraries into APA-style references.

Options include:

-   Author/year/title sorting
-   Numbered references
-   DOI URL support
-   Plain-text export

### Quality Control

-   Duplicate detection
-   Confidence scoring
-   Review file for uncertain records
-   Scan report (CSV)
-   List of unreadable PDFs

------------------------------------------------------------------------

## Installation

Clone the repository:

``` bash
git clone https://github.com/<your-username>/PaperForge.git
cd PaperForge
```

Install the dependencies:

``` bash
pip install -r requirements.txt
```

------------------------------------------------------------------------

## Usage

### Scan a PDF library

``` bash
python pdf_to_bib.py "/path/to/Papers" --output references.bib
```

### Scan PDFs and export APA references

``` bash
python pdf_to_bib.py "/path/to/Papers" \
    --output references.bib \
    --apa-output references_APA.txt
```

### Convert an existing BibTeX library

``` bash
python pdf_to_bib.py \
    --bib-input references.bib \
    --apa-output references_APA.txt
```

------------------------------------------------------------------------

## Output Files

  File                      Description
  ------------------------- ----------------------------------
  `references.bib`          High-confidence BibTeX entries
  `references_review.bib`   Entries requiring manual review
  `scan_report.csv`         Scan summary
  `unrecognized_pdfs.txt`   PDFs that could not be processed
  `references_APA.txt`      APA-style references

------------------------------------------------------------------------

## Roadmap

Planned features include:

-   RIS export
-   CSL-JSON export
-   BibLaTeX support
-   IEEE, MLA, Chicago, Vancouver citation styles
-   PubMed metadata lookup
-   Semantic Scholar integration
-   ORCID support
-   Google Books integration
-   Automatic PDF renaming
-   Automatic folder organization
-   Citation-key customization
-   OCR support for scanned PDFs
-   Graphical user interface
-   Batch metadata correction
-   Duplicate merging
-   Zotero synchronization
-   Mendeley synchronization
-   JabRef integration
-   AI-assisted metadata completion
-   Automatic keyword extraction
-   Topic classification
-   Research library analytics

------------------------------------------------------------------------

## Contributing

Contributions are welcome.

If you find a bug or have an idea for a new feature, please open an
issue or submit a pull request.

------------------------------------------------------------------------

## License

This project is released under the MIT License.

------------------------------------------------------------------------

## Citation

If you use PaperForge in your research, please consider citing this
repository once a formal software release or DOI is available.
