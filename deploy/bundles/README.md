# Demo bundles served by the deployed site

Each folder here is one real evaluation of a `codeatlas-fyp/codeatlas-sandbox` pull request:
`<bundle_hash>/bundle.json` (the frozen evidence) and `<bundle_hash>/verdict.json` (the result).
The Docker image copies this folder to `/app/bundles`, so the live site shows these cases.

Never edit these files by hand: the folder name is the SHA-256 of `bundle.json`, and any change
makes the site show the case as tampered (that is the replay check working).

## Refreshing them

From the `codeatlas` folder, with the GitHub and Jira tokens set in your shell:

```
foreach ($n in 2,3,4,5,6) {
  uv run codeatlas collect --repo codeatlas-fyp/codeatlas-sandbox --pr $n `
    --repo-path ..\codeatlas-sandbox `
    --policy ..\codeatlas-local\policy.yaml `
    --identity-map ..\codeatlas-local\identity_map.yaml `
    --store ..\codeatlas-local\bundles
}
```

Then replace the folders here with the ones from `..\codeatlas-local\bundles` and run
`uv run pytest tests/unit/test_demo_bundles.py`. It checks every bundle replays to its stored
verdict, renders on every page, and contains no token.
