// Dracula theme colors for tag chips
const TAG_COLORS = ["#8be9fd","#ff79c6","#50fa7b","#f1fa8c","#bd93f9","#ffb86c","#ff5555","#6272a4"];

export function tagColor(tag: string): string {
  let hash = 0;
  for (const ch of tag) hash = ((hash << 5) - hash + ch.charCodeAt(0)) | 0;
  return TAG_COLORS[Math.abs(hash) % TAG_COLORS.length];
}
