#!/bin/bash
#
# PostToolUse hook: 每编辑 5 次 web/ 下的 ts/tsx 文件后，自动跑 Playwright 测试
# 异步执行，不阻塞 Claude
#

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.file // empty')

# 只关注 web/ 下的 ts/tsx 文件
case "$FILE_PATH" in
  */web/app/*.ts|*/web/app/*.tsx) ;;
  *) exit 0 ;;
esac

# 计数器文件
COUNTER_FILE="/tmp/claude-playwright-counter"
LOCK_FILE="/tmp/claude-playwright.lock"
THRESHOLD=5

# 增加计数
if [ -f "$COUNTER_FILE" ]; then
  COUNT=$(<"$COUNTER_FILE")
  COUNT=$((COUNT + 1))
else
  COUNT=1
fi
echo "$COUNT" > "$COUNTER_FILE"

# 未达阈值，静默退出
if [ $((COUNT % THRESHOLD)) -ne 0 ]; then
  exit 0
fi

# 避免重复运行（上一轮还没跑完）
if [ -f "$LOCK_FILE" ]; then
  exit 0
fi
touch "$LOCK_FILE"

# 跑测试
cd "$(dirname "$0")/../../web" || exit 1
RESULT=$(npx playwright test --reporter=line 2>&1)
EXIT_CODE=$?
rm -f "$LOCK_FILE"

if [ $EXIT_CODE -eq 0 ]; then
  PASSED=$(echo "$RESULT" | grep -o '[0-9]* passed' | head -1)
  echo "{\"systemMessage\": \"Playwright: $PASSED (edit #$COUNT)\"}"
else
  FAILED=$(echo "$RESULT" | grep -E 'failed|Error' | head -3 | tr '\n' ' ')
  echo "{\"systemMessage\": \"Playwright FAILED (edit #$COUNT): $FAILED\"}"
fi
