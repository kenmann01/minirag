---
name: call-scan
description: >
  Scan a Java source tree into graph.json of unambiguous method-call edges.
  Use when mapping a repository, building a call graph for the minirag gate,
  or replacing Graphify. The repository chooses the tree through an adapter;
  this skill only scans the tree it is given.
---

# Call scan

Map a Java tree by running the bundled script. Do not read Java files to invent nodes or edges. The script is the map. It does not know which repository it is looking at.

## Run

From the minirag repo root:

```bash
python skills/call-scan/scripts/scan.py <tree> --out <dir> --base <repo>
```

`<tree>` is the source directory to scan. `--base` is the repository root stored on each node's `source_file`. Omit `--base` to store paths relative to `<tree>`.

Which tree to pass is the adapter's job. The Fineract adapter in `app/adapter.py` passes the loans package. Another repository is another adapter, not a change to this skill.

A missing tree is a failure. The script exits nonzero and writes nothing. Do not create an empty graph to fill the gap.

## Output

`<dir>/graph.json` has `nodes` and `links`.

- A node id is `Type.method`. Its label is the method name. `source_file` is the path relative to `--base`.
- A link has `relation` `calls` and `confidence` `EXTRACTED`. The source is the enclosing method.

A call is kept only when it is unambiguous inside the tree: `Type.method(` matches that node, or a bare `method(` matches exactly one declared method. Every other call is omitted. Do not add those back, and do not tag anything `INFERRED`.
