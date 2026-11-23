"use client";
import { Streamdown } from "streamdown";

export function Markdown({ text, streaming = false }: { text: string; streaming?: boolean }) {
  return (
    <div className="prose-sm max-w-none break-words [&_p]:my-1 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5 [&_strong]:font-semibold">
      <Streamdown mode={streaming ? "streaming" : "static"}>{text}</Streamdown>
    </div>
  );
}
