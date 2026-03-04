import { useRef, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Message } from "../lib/types";
import { OutputEmbed } from "./OutputEmbed";

interface Props {
  messages: Message[];
  status: string;
  statusMessage: string;
}

export function MessageThread({ messages, status, statusMessage }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, statusMessage]);

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-6 space-y-6">
        {messages.length === 0 && (
          <div className="flex items-center justify-center min-h-[60vh] text-zinc-500">
            <div className="text-center space-y-2">
              <p className="text-2xl font-semibold text-zinc-300">
                What can I help you with?
              </p>
              <p className="text-sm">
                Describe what you need — Anton will figure out the rest.
              </p>
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div key={msg.id}>
            {msg.role === "user" ? (
              <div className="flex justify-end">
                <div className="bg-indigo-600 rounded-2xl rounded-br-md px-4 py-2.5 max-w-[80%]">
                  <p className="whitespace-pre-wrap text-sm leading-relaxed">
                    {typeof msg.content === "string"
                      ? msg.content
                      : "[attachment]"}
                  </p>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="prose prose-invert prose-sm max-w-none prose-p:leading-relaxed prose-pre:bg-zinc-900 prose-pre:border prose-pre:border-zinc-800 prose-code:text-indigo-300 prose-a:text-indigo-400">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {msg.content}
                  </ReactMarkdown>
                </div>
                {msg.outputs.map((output, i) => (
                  <OutputEmbed key={i} output={output} />
                ))}
              </div>
            )}
          </div>
        ))}

        {status === "streaming" && statusMessage && (
          <div className="flex items-center gap-2.5 text-zinc-400 text-sm py-1">
            <div className="animate-spin h-4 w-4 border-2 border-zinc-500 border-t-indigo-400 rounded-full" />
            <span>{statusMessage}</span>
          </div>
        )}

        <div ref={endRef} />
      </div>
    </div>
  );
}
