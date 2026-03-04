import { ExternalLink, Download } from "lucide-react";
import type { Output } from "../lib/types";

const API_BASE = `http://${window.location.hostname}:8000`;

interface Props {
  output: Output;
}

export function OutputEmbed({ output }: Props) {
  const url = `${API_BASE}${output.url}`;
  const filename = output.path.split("/").pop() || "file";

  if (output.kind === "html") {
    return (
      <div className="rounded-xl overflow-hidden border border-zinc-700/50">
        <div className="flex items-center justify-between px-4 py-2 bg-zinc-800/50 text-xs text-zinc-400">
          <span>{filename}</span>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 hover:text-zinc-200 transition-colors"
          >
            Open in new tab <ExternalLink size={12} />
          </a>
        </div>
        <iframe
          src={url}
          title={filename}
          className="w-full h-[400px] bg-white"
          sandbox="allow-scripts allow-same-origin"
        />
      </div>
    );
  }

  if (output.kind === "image") {
    return (
      <div className="rounded-xl overflow-hidden border border-zinc-700/50">
        <img
          src={url}
          alt={filename}
          className="max-w-full"
          loading="lazy"
        />
      </div>
    );
  }

  return (
    <a
      href={url}
      download
      className="inline-flex items-center gap-2 text-sm text-indigo-400 hover:text-indigo-300 transition-colors"
    >
      <Download size={14} />
      {filename}
    </a>
  );
}
