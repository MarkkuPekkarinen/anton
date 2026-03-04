import { useAnton } from "../hooks/useAnton";
import { MessageThread } from "./MessageThread";
import { InputArea } from "./InputArea";

export function Chat() {
  const { messages, status, statusMessage, sendMessage, uploadFile } =
    useAnton();

  return (
    <div className="h-full flex flex-col">
      <header className="shrink-0 px-6 py-4 border-b border-zinc-800/60">
        <div className="max-w-3xl mx-auto flex items-center gap-3">
          <div className="h-8 w-8 rounded-lg bg-indigo-600 flex items-center justify-center text-sm font-bold">
            A
          </div>
          <h1 className="text-lg font-semibold tracking-tight">Anton</h1>
        </div>
      </header>

      <MessageThread
        messages={messages}
        status={status}
        statusMessage={statusMessage}
      />

      <InputArea
        onSend={sendMessage}
        onUpload={uploadFile}
        disabled={status === "streaming" || status === "connecting"}
      />
    </div>
  );
}
