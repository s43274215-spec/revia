# Revia Development Guide

## Project Overview

Revia is a desktop-oriented learning application built as a Web App.

The goal of Revia is not to summarize documents automatically, but to help users build structured knowledge through progressive learning.

Version 1 focuses on an excellent reading and review experience.

---

## Tech Stack

- Next.js (App Router)
- React
- TypeScript
- Tailwind CSS

Future:
- DeepSeek API
- Local database
- Desktop packaging

---

## Development Principles

- Never add features that are not explicitly required.
- Never redesign existing interactions without confirmation.
- Follow the existing UI style and interaction logic.
- Prefer simple, maintainable implementations.
- Keep components reusable.

---

## Product Principles

The product follows these principles:

1. Reading first.
2. Minimal visual distractions.
3. Knowledge should always be editable.
4. Editing should never interrupt reading.
5. One source of truth for data.

---

## UI Principles

The interface should feel:

- Warm
- Calm
- Lightweight
- Modern
- Comfortable for long reading sessions

Avoid:

- Strong gradients
- Overly saturated colors
- Heavy shadows
- Enterprise dashboard style

---

## Interaction Principles

Important interactions that must remain consistent:

- The right drawer overlays the reading area instead of changing layout.
- Editing always happens inside the right drawer.
- The drawer supports both single-version editing and global editing.
- Global editing uses collapsible sections for the three versions.
- Keyword mode displays the drawer.
- Other modes hide the drawer.

---

## Coding Rules

- Prefer functional components.
- Use TypeScript types whenever possible.
- Avoid duplicated logic.
- Keep components small.
- Separate UI and business logic.

---

## Before Writing Code

Before implementing a new feature:

1. Understand the existing architecture.
2. Reuse existing components whenever possible.
3. Explain the implementation plan before making major structural changes.

Never rewrite large sections of code unless necessary.
