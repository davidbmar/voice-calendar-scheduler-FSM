#!/bin/bash
# Build Project Memory index from session files
# Pure bash + jq implementation — no other dependencies

set -e

INDEX_DIR="docs/project-memory/.index"
SESSIONS_DIR="docs/project-memory/sessions"

STOP_WORDS="the is at which on a an and or but in with to for of as by this that from was were been have has had do does did will would could should may might must can be are am it its we they you he she what when where who why how not no yes all any each every some none also just only very much more most"

mkdir -p "$INDEX_DIR"

echo "Building Project Memory index..."

SESSION_FILES=()
while IFS= read -r f; do
    SESSION_FILES+=("$f")
done < <(find "$SESSIONS_DIR" -name "S-*.md" 2>/dev/null | sort)

SESSION_COUNT=${#SESSION_FILES[@]}
echo "Found $SESSION_COUNT session files"

rm -f "$INDEX_DIR/metadata.json.tmp"

for file in "${SESSION_FILES[@]}"; do
    SESSION_ID=$(basename "$file" .md)
    CONTENT=$(cat "$file")
    TITLE=$(echo "$CONTENT" | grep -m1 "^Title:" | sed 's/^Title:[[:space:]]*//' || echo "")
    DATE=$(echo "$CONTENT" | grep -m1 "^Date:" | sed 's/^Date:[[:space:]]*//' || echo "")
    AUTHOR=$(echo "$CONTENT" | grep -m1 "^Author:" | sed 's/^Author:[[:space:]]*//' || echo "")
    GOAL=$(echo "$CONTENT" | sed -n '/^## Goal/,/^## /{/^## Goal/d;/^## /d;p;}' | tr '\n' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' | head -c 500)
    SECTIONS=$(echo "$CONTENT" | sed -n '/^## Goal/,/^## Links/{/^## Links/d;p;}' | tr '\n' ' ')
    KEYWORDS=$(echo "$SECTIONS" | \
        tr '[:upper:]' '[:lower:]' | \
        sed 's/[^a-z0-9 -]/ /g' | \
        tr -s ' ' '\n' | \
        sort -u | \
        while read -r word; do
            [ ${#word} -lt 3 ] && continue
            echo "$word" | grep -qE '^[0-9]+$' && continue
            echo " $STOP_WORDS " | grep -q " $word " && continue
            echo "$word"
        done | \
        head -50)
    KEYWORDS_JSON=$(echo "$KEYWORDS" | jq -R . | jq -s .)
    jq -n \
        --arg sid "$SESSION_ID" \
        --arg title "$TITLE" \
        --arg file "$file" \
        --arg date "$DATE" \
        --arg author "$AUTHOR" \
        --arg goal "$GOAL" \
        --argjson keywords "$KEYWORDS_JSON" \
        '{sessionId: $sid, title: $title, file: $file, date: $date, author: $author, goal: $goal, keywords: $keywords}' \
        >> "$INDEX_DIR/metadata.json.tmp"
done

if [ -f "$INDEX_DIR/metadata.json.tmp" ]; then
    jq -s '.' "$INDEX_DIR/metadata.json.tmp" > "$INDEX_DIR/metadata.json"
    rm "$INDEX_DIR/metadata.json.tmp"
else
    echo "[]" > "$INDEX_DIR/metadata.json"
fi

jq '
  reduce .[] as $s ({};
    reduce $s.keywords[] as $kw (.;
      .[$kw] = ((.[$kw] // []) + [$s.sessionId] | unique)
    )
  )
' "$INDEX_DIR/metadata.json" > "$INDEX_DIR/keywords.json"

> "$INDEX_DIR/sessions.txt"
for file in "${SESSION_FILES[@]}"; do
    SESSION_ID=$(basename "$file" .md)
    echo "=== $SESSION_ID ===" >> "$INDEX_DIR/sessions.txt"
    cat "$file" >> "$INDEX_DIR/sessions.txt"
    echo "" >> "$INDEX_DIR/sessions.txt"
done

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$INDEX_DIR/last-updated.txt"

echo "Index built: $SESSION_COUNT session(s)"
