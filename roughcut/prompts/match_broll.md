# Match b-roll to a sentence

Given one sentence of interview dialog and a library of b-roll clips with
structured descriptions, suggest 0–3 inserts that visually support the
sentence. Prefer literal/illustrative matches over abstract ones, and avoid
re-using clips back-to-back when alternatives exist.

Return picks via the `match_broll` tool.

## Sentence

{{sentence}}

## Library

{{library}}
