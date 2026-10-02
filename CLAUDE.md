# easyVmafPlus

easyVmafPlus is a fork of `gdavila/easyVmaf`. It adds hardware accelerated decoding on both inputs, a system-wide `easyVmafPlus` command installed through a symlink, and its own multi-arch Docker image on `ghcr.io/marcelpoelstra/easyvmafplus`.

## Project knowledge base

All knowledge about this project lives in `workdocuments/`. The directory is in `.gitignore` and exists only locally.

| Path | Content |
|---|---|
| `workdocuments/knowledge/architecture.md` | How easyVmafPlus works: files, CLI flags, run flow, deinterlace rules, ffmpeg command, VMAF models, output files, local environment |
| `workdocuments/knowledge/fork-improvements.md` | Fork versus upstream: remotes, fork point, fork commits, fixes, the rename to easyVmafPlus, measured behaviour of each improvement, Docker image and GHCR publishing, upstream commits after the fork point |
| `workdocuments/knowledge/verification.md` | Regression baseline: sample run scores, wrapper, install and uninstall results, Docker image and workflow checks |
| `workdocuments/graphify-out/graph.json` | graphify knowledge graph of the code, `README.md`, the README diagrams and the knowledge notes |
| `workdocuments/graphify-out/GRAPH_REPORT.md` | Graph report: communities, god nodes, suggested questions |
| `workdocuments/graphify-out/graph.html` | Interactive graph |

## Before any change

1. Read `workdocuments/knowledge/architecture.md`, `workdocuments/knowledge/fork-improvements.md` and `workdocuments/knowledge/verification.md`.
2. Query the graph for the area being changed. Run from the repository root:

```bash
graphify query "<question>" --graph workdocuments/graphify-out/graph.json
graphify explain "<symbol>" --graph workdocuments/graphify-out/graph.json
graphify path "<A>" "<B>" --graph workdocuments/graphify-out/graph.json
graphify affected "<symbol>" --graph workdocuments/graphify-out/graph.json
```

## Recording new knowledge

Every verified fact learned while working goes into the knowledge notes in the same turn. Add it to the note it belongs to, or create `workdocuments/knowledge/<topic>.md` for a new topic. Each fact cites a file and line with the git ref, a command and its output, or a vendor document. Update the `Ref:` line of a note when its line numbers move. After changing a note or the code, rebuild the graph.

## Rebuilding the graph

Run the graphify skill pipeline from the repository root, with these settings:

1. Export `GRAPHIFY_OUT=workdocuments/graphify-out` for every graphify command.
2. Replace every `graphify-out/` path in the skill steps with `workdocuments/graphify-out/`.
3. Use INPUT_PATH `.` and the interpreter stored in `workdocuments/graphify-out/.graphify_python`.
4. In Step 2, call `detect(Path('.'), extra_excludes=['video_samples/'])`.
5. Append the absolute path of every `workdocuments/knowledge/*.md` to `files['document']`, then set `total_files` to the sum of all file lists. `.gitignore` excludes `workdocuments/`, so detect does not find the notes by itself.
6. When `to_json` refuses to shrink `graph.json`, count nodes per `source_file` in the new extraction. If every corpus file is present, write with `to_json(..., force=True)`.
