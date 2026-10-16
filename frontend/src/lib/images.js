export const MAX_IMAGES = 4;
const OK_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];
const MAX_EDGE = 1568;
const MAX_BYTES = 5 * 1024 * 1024;

/** Shrink large photos in the browser before upload (faster, cheaper for the AI). Falls back to the original file. */
export async function prepareImage(file) {
  if (!OK_TYPES.includes(file.type)) throw new Error("Only PNG, JPEG, WEBP or GIF images are supported.");
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, MAX_EDGE / Math.max(bmp.width, bmp.height));
    if (scale === 1 && file.size <= 1_500_000 && file.type !== "image/gif") return file;
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff"; // transparent PNGs become white instead of black
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((res) => canvas.toBlob(res, "image/jpeg", 0.85));
    if (blob) return new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" });
  } catch { /* fall through: send the original */ }
  if (file.size > MAX_BYTES) throw new Error("That image is too large (max 5 MB).");
  return file;
}
