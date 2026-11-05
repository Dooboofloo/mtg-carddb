# MTG CardDB

A Python library for indexing and querying *Magic: The Gathering* card data using composable set operations.

## Overview

MTG CardDB aims to provide a fast, flexible, and intuitive way to work with Magic card data in Python. Its central design is an inverted-index query engine: card attributes map to sets of card identifiers, allowing complex queries to be constructed using ordinary Python set operations.

The project emphasizes a simple public API, efficient repeated queries, and a clear separation between card data and application-specific rules.

## Planned Features

- **Set-based queries:** Combine indexed card attributes through intersections, unions, and differences.
- **Card classifications:** Search by color identity, card type, subtype, keywords, format legality, and Oracle tags.
- **Hierarchical tags:** Query broad card functions through related and more specific tags.
- **SQLite persistence:** Store and manage normalized card data locally.
- **Configurable filtering:** Let applications define their own card pools and exclusions without modifying the underlying dataset.
- **Offline operation:** Query locally stored data without requiring an active connection.

## Project Status

**Early development — not yet released.**

The project is currently in its initial design phase. The planned initial data source is Scryfall.

## Development

The project uses Python 3.11 or newer.

To install a local checkout in editable mode, run from the repository root:

`python -m pip install -e .`

The package is not yet ready for use as a dependency in production projects.

## License

This project's original source code is licensed under the MIT License. See [LICENSE](LICENSE).

*Magic: The Gathering* is a trademark of Wizards of the Coast. Card data and other third-party materials are subject to their respective terms. This project is not affiliated with or endorsed by Wizards of the Coast or Scryfall.