# Future Improvements

This file tracks potential future improvements and enhancements for the coloring app. Priorities and rough effort estimates are included to help plan follow-up work.

## Medium priority / medium effort

- Implement an alpha-threshold preprocessing step before flood-fill
  - Why: binarize edges to remove remaining anti-aliased artifacts
  - Effort: medium (image processing + testing)
- Add an interactive tolerance/dilation post-process to remove tiny specks
  - Why: cleans up small islands from anti-aliasing or low-res images
  - Effort: medium
- Add an undo/redo stack for fill actions
  - Why: improves UX for mistakes
  - Effort: medium

## Lower priority / larger effort

- Offload heavy pixel ops to a Web Worker
  - Why: keep UI responsive for large canvases and many operations
  - Effort: large (worker + message protocol + transferables)
- Add multiple save slots and named projects (localStorage or IndexedDB)
  - Why: users can maintain multiple works-in-progress
  - Effort: medium-large
- Export as high-resolution PNG and PDF
  - Why: produce print-ready output
  - Effort: medium

## Nice-to-have UX & platform features

- Mobile/touch support (touch events, larger hit targets)
- Color palette saving & preset palettes
- Eyedropper tool to pick colors from the canvas