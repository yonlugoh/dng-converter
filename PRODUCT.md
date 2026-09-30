# Product

<!-- impeccable:product-schema 1 -->

## Platform

Windows desktop (Python + tkinter, shipped as a PyInstaller exe)

## Users
One photographer, at the end of a shoot, is importing a card of Sony RAW+JPEG pairs before editing them.

## Product Purpose
The user picks a folder and runs one action. JPGs go into `jpg\`, and every ARW gets a lossless DNG next to it. Success means every DNG is present, failures are clearly named, and it is always safe to run again.

## Capabilities and Constraints
See CLAUDE.md for the hard rules: top level only, ARWs are never touched, idempotent re-runs, and a background worker thread feeding a queue that the Tk thread polls.

## Product Principles
- The folder and the progress come first. Everything else stays quiet.
- A failure must be impossible to miss. A success can be understated.
