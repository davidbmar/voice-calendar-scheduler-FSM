#!/bin/bash
# Install git hooks for Project Memory system

set -e

HOOKS_DIR=".git/hooks"
HOOK_FILE="$HOOKS_DIR/pre-commit"

echo "Setting up Project Memory git hooks..."

if [ ! -d ".git" ]; then
    echo "Error: Not in a git repository root"
    exit 1
fi

cat > "$HOOK_FILE" << 'EOF'
#!/bin/sh
# Pre-commit hook: rebuild index before allowing commit

echo "Rebuilding Project Memory index..."
./scripts/build-index.sh

# Stage updated index files if they changed
git add docs/project-memory/.index/*.json docs/project-memory/.index/*.txt 2>/dev/null || true
EOF

chmod +x "$HOOK_FILE"

echo "Pre-commit hook installed at $HOOK_FILE"
