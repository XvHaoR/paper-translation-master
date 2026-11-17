These tests use jsdom with resource loading disabled. They exercise DOM grouping,
editor controls and PDF action events; they do not launch a browser or OCR model.

Install once with `npm ci --prefix tests/dom`, then run the Python tests normally,
or `npm test --prefix tests/dom`. The lock file pins the DOM test dependency.
