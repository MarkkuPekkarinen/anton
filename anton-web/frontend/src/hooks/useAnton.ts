import { useState, useRef, useCallback, useEffect } from "react";
import type { Message, Output, UploadedFile } from "../lib/types";

const WS_URL = `ws://${window.location.hostname}:8000/api/chat`;
const API_BASE = `http://${window.location.hostname}:8000/api`;

type Status = "idle" | "connecting" | "streaming";

export function useAnton() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<Status>("connecting");
  const [statusMessage, setStatusMessage] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const assistantRef = useRef<{
    id: string;
    text: string;
    outputs: Output[];
  } | null>(null);

  const handleWsMessage = useCallback((evt: MessageEvent) => {
    const data = JSON.parse(evt.data);

    switch (data.type) {
      case "session_created":
        setSessionId(data.session_id);
        setStatus("idle");
        break;

      case "session_resumed":
        setSessionId(data.session_id);
        setStatus("idle");
        break;

      case "history_message":
        if (data.role === "user" || data.role === "assistant") {
          const content =
            typeof data.content === "string"
              ? data.content
              : JSON.stringify(data.content);
          setMessages((prev) => [
            ...prev,
            { id: crypto.randomUUID(), role: data.role, content, outputs: [] },
          ]);
        }
        break;

      case "text_delta": {
        if (!assistantRef.current) {
          const id = crypto.randomUUID();
          assistantRef.current = { id, text: "", outputs: [] };
          setMessages((prev) => [
            ...prev,
            { id, role: "assistant", content: "", outputs: [] },
          ]);
        }
        assistantRef.current.text += data.text;
        const { id, text } = assistantRef.current;
        setMessages((prev) =>
          prev.map((m) => (m.id === id ? { ...m, content: text } : m)),
        );
        setStatus("streaming");
        setStatusMessage("");
        break;
      }

      case "status":
        setStatus("streaming");
        setStatusMessage(data.message || "");
        break;

      case "outputs":
        if (assistantRef.current) {
          assistantRef.current.outputs.push(...data.files);
          const outputs = [...assistantRef.current.outputs];
          const msgId = assistantRef.current.id;
          setMessages((prev) =>
            prev.map((m) => (m.id === msgId ? { ...m, outputs } : m)),
          );
        }
        break;

      case "complete":
        setStatus("idle");
        setStatusMessage("");
        assistantRef.current = null;
        break;

      case "context_compacted":
        break;

      case "error":
        setStatus("idle");
        setStatusMessage("");
        assistantRef.current = null;
        break;
    }
  }, []);

  useEffect(() => {
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "create_session" }));
    };

    ws.onmessage = handleWsMessage;

    ws.onclose = () => {
      setStatus("idle");
    };

    return () => {
      ws.close();
    };
  }, [handleWsMessage]);

  const sendMessage = useCallback(
    (content: string, files?: UploadedFile[]) => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) return;

      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: "user", content, outputs: [] },
      ]);
      setStatus("streaming");
      setStatusMessage("Thinking...");

      ws.send(
        JSON.stringify({
          type: "message",
          content,
          files: files?.map((f) => ({
            id: f.id,
            name: f.name,
            path: f.path,
            type: f.type,
          })),
        }),
      );
    },
    [],
  );

  const uploadFile = useCallback(async (file: File): Promise<UploadedFile> => {
    const formData = new FormData();
    formData.append("file", file);
    const resp = await fetch(`${API_BASE}/files`, {
      method: "POST",
      body: formData,
    });
    return await resp.json();
  }, []);

  const resumeSession = useCallback((sid: string) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    setMessages([]);
    assistantRef.current = null;
    ws.send(JSON.stringify({ type: "resume_session", session_id: sid }));
  }, []);

  return {
    messages,
    status,
    statusMessage,
    sessionId,
    sendMessage,
    uploadFile,
    resumeSession,
  };
}
