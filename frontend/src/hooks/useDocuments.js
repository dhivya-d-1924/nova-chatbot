import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";

/** Uploaded documents, which of them the chat may use (selected), upload progress and server limits. */
export function useDocuments(setDocIds, notify) {
  const [docs, setDocs] = useState([]);
  const [selected, setSelected] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploads, setUploads] = useState([]); // {key, name, state: uploading|indexing|error, progress, error}
  const [error, setError] = useState(null);
  const [config, setConfig] = useState({ extensions: ["pdf", "docx", "txt", "md", "csv"], max_mb: 15, ocr: false, semantic: false });
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    try {
      const [d, cfg] = await Promise.all([api.listDocuments(), api.documentsConfig().catch(() => null)]);
      setDocs(d);
      setSelected(d.map((x) => x.id));
      if (cfg) setConfig(cfg);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  useEffect(() => { setDocIds(selected); }, [selected, setDocIds]);

  const toggle = useCallback((id) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id])), []);
  const setAll = useCallback((on) => setSelected(on ? docs.map((d) => d.id) : []), [docs]);

  const upload = useCallback(async (files) => {
    for (const file of files) {
      const key = `${file.name}-${Math.random()}`;
      const ext = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
      if (!config.extensions.includes(ext)) {
        setUploads((u) => [...u, { key, name: file.name, state: "error", error: `Unsupported type. Use ${config.extensions.map((x) => x.toUpperCase()).join(", ")}.` }]);
        continue;
      }
      if (file.size > config.max_mb * 1024 * 1024) {
        setUploads((u) => [...u, { key, name: file.name, state: "error", error: `Too large (max ${config.max_mb} MB).` }]);
        continue;
      }
      setUploads((u) => [...u, { key, name: file.name, state: "uploading", progress: 0 }]);
      try {
        const d = await api.uploadDocument(file, (p) =>
          setUploads((u) => u.map((i) => (i.key === key ? { ...i, progress: p, state: p >= 100 ? "indexing" : "uploading" } : i))));
        setDocs((x) => [d, ...x]);
        setSelected((s) => [...s, d.id]);
        setUploads((u) => u.filter((i) => i.key !== key));
        notify?.("success", `“${d.filename}” is ready${d.version > 1 ? ` (version ${d.version})` : ""}.`);
      } catch (e) {
        setUploads((u) => u.map((i) => (i.key === key ? { ...i, state: "error", error: e.message } : i)));
      }
    }
  }, [config, notify]);

  const dismissUpload = useCallback((key) => setUploads((u) => u.filter((i) => i.key !== key)), []);

  const remove = useCallback(async (id) => {
    setBusyId(id);
    try {
      await api.deleteDocument(id);
      setDocs((x) => x.filter((d) => d.id !== id));
      setSelected((s) => s.filter((x) => x !== id));
      notify?.("success", "Document deleted.");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }, [notify]);

  const reindex = useCallback(async (id) => {
    setBusyId(id);
    try {
      const d = await api.reindexDocument(id);
      setDocs((x) => x.map((y) => (y.id === id ? d : y)));
      notify?.("success", "Document re-indexed.");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusyId(null);
    }
  }, [notify]);

  return { docs, selected, loading, uploads, error, config, busyId, toggle, setAll, upload, dismissUpload, remove, reindex };
}
