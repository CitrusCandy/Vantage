"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { PlusCircle, Sparkles, AlertCircle } from "lucide-react";
import { Modal } from "../common/Modal";
import { Button } from "../common/Button";
import { createTopic, triggerIngestion } from "@/lib/api";

interface CreateTopicModalProps {
  isOpen: boolean;
  onClose: () => void;
  onTopicCreated?: (slug: string) => void;
}

export const CreateTopicModal: React.FC<CreateTopicModalProps> = ({
  isOpen,
  onClose,
  onTopicCreated,
}) => {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [autoIngest, setAutoIngest] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;

    setIsLoading(true);
    setError(null);

    try {
      const topic = await createTopic({ title: title.trim() });

      if (autoIngest) {
        // Fire ingestion in background
        triggerIngestion(topic.slug, 30).catch(() => null);
      }

      setTitle("");
      onClose();
      if (onTopicCreated) {
        onTopicCreated(topic.slug);
      }
      router.push(`/topics/${topic.slug}`);
    } catch (err: any) {
      setError(err.message || "Failed to create topic");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Analyze New Discourse Topic"
      maxWidth="max-w-lg"
    >
      <form onSubmit={handleSubmit} className="space-y-5">
        <p className="text-xs text-slate-400">
          Enter a topic, current event, or public discourse query. Vantage will
          scrape Google News RSS, Reddit, and X into staging tables and synthesize
          unbiased perspectives.
        </p>

        {error && (
          <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-400 text-xs flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="space-y-1.5">
          <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
            Topic Title / Search Query
          </label>
          <input
            type="text"
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. EU AI Act Enforcement, Solid-State EV Batteries..."
            className="w-full px-4 py-2.5 rounded-xl bg-slate-900 border border-slate-700 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500"
          />
        </div>

        <div className="flex items-center gap-2.5 p-3 rounded-xl bg-surface-light border border-surface-border">
          <input
            type="checkbox"
            id="autoIngest"
            checked={autoIngest}
            onChange={(e) => setAutoIngest(e.target.checked)}
            className="rounded border-slate-700 text-indigo-600 focus:ring-indigo-500 w-4 h-4 bg-slate-900 cursor-pointer"
          />
          <label
            htmlFor="autoIngest"
            className="text-xs text-slate-300 cursor-pointer select-none"
          >
            Automatically trigger multi-source ingestion on creation
          </label>
        </div>

        <div className="flex justify-end gap-3 pt-3 border-t border-slate-800">
          <Button
            type="button"
            variant="ghost"
            onClick={onClose}
            disabled={isLoading}
          >
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            isLoading={isLoading}
            icon={<Sparkles className="w-4 h-4" />}
          >
            Create & Ingest
          </Button>
        </div>
      </form>
    </Modal>
  );
};
