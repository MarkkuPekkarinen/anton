import { useState, useRef, useCallback } from "react";
import { Send, Paperclip, X } from "lucide-react";
import type { UploadedFile } from "../lib/types";

interface Props {
  onSend: (content: string, files?: UploadedFile[]) => void;
  onUpload: (file: File) => Promise<UploadedFile>;
  disabled: boolean;
}

export function InputArea({ onSend, onUpload, disabled }: Props) {
  const [text, setText] = useState("");
  const [files, setFiles] = useState<UploadedFile[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed && files.length === 0) return;
    onSend(trimmed, files.length > 0 ? files : undefined);
    setText("");
    setFiles([]);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setText(e.target.value);
    const el = e.target;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
  };

  const handleFileSelect = async (fileList: FileList) => {
    setUploading(true);
    try {
      const uploaded = await Promise.all(
        Array.from(fileList).map((f) => onUpload(f)),
      );
      setFiles((prev) => [...prev, ...uploaded]);
    } finally {
      setUploading(false);
    }
  };

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      if (e.dataTransfer.files.length > 0) {
        await handleFileSelect(e.dataTransfer.files);
      }
    },
    [onUpload],
  );

  return (
    <div
      className="shrink-0 border-t border-zinc-800/60 bg-zinc-950"
      onDragOver={(e) => e.preventDefault()}
      onDrop={handleDrop}
    >
      <div className="max-w-3xl mx-auto p-4">
        {files.length > 0 && (
          <div className="flex gap-2 mb-3 flex-wrap">
            {files.map((f, i) => (
              <div
                key={i}
                className="bg-zinc-800 rounded-lg px-3 py-1.5 text-xs flex items-center gap-2 text-zinc-300"
              >
                <span className="max-w-[200px] truncate">{f.name}</span>
                <button
                  onClick={() =>
                    setFiles((prev) => prev.filter((_, j) => j !== i))
                  }
                  className="text-zinc-500 hover:text-zinc-200 transition-colors"
                >
                  <X size={14} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div className="flex items-end gap-2">
          <button
            onClick={() => fileInputRef.current?.click()}
            className="p-2.5 text-zinc-500 hover:text-zinc-300 transition-colors rounded-lg hover:bg-zinc-800/50"
            disabled={disabled || uploading}
          >
            <Paperclip size={20} />
          </button>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) =>
              e.target.files && handleFileSelect(e.target.files)
            }
          />

          <textarea
            ref={textareaRef}
            value={text}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder="Ask Anton anything..."
            rows={1}
            disabled={disabled}
            className="flex-1 bg-zinc-900 border border-zinc-700/50 rounded-xl px-4 py-3 resize-none
                       text-sm leading-relaxed
                       focus:outline-none focus:border-indigo-500/50 focus:ring-1 focus:ring-indigo-500/25
                       placeholder-zinc-500
                       disabled:opacity-50 overflow-y-auto transition-colors"
          />

          <button
            onClick={handleSend}
            disabled={disabled || (!text.trim() && files.length === 0)}
            className="p-2.5 bg-indigo-600 hover:bg-indigo-500 rounded-xl transition-colors
                       disabled:opacity-30 disabled:hover:bg-indigo-600"
          >
            <Send size={20} />
          </button>
        </div>
      </div>
    </div>
  );
}
