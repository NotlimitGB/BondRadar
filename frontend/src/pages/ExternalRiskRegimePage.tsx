import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";

import { api, normalizeApiError } from "../api/client";
import type {
  ExternalRiskRegime,
  ExternalRiskRegimeMode,
  ExternalRiskRegimeUpdateRequest,
} from "../api/types";
import { ErrorState, LoadingState } from "../components/StateBlocks";
import { formatDateTime } from "../utils/livePaperFormat";

type RegimeFormState = {
  mode: ExternalRiskRegimeMode;
  reason: string;
  source: string;
  expires_at: string;
};

const modeLabels: Record<ExternalRiskRegimeMode, string> = {
  normal: "Обычный режим",
  elevated: "Повышенный внешний риск",
  severe: "Жёсткий внешний риск",
};

const modeDescriptions: Record<ExternalRiskRegimeMode, string> = {
  normal: "Обычный режим: virtual paper flow может работать в штатном режиме.",
  elevated:
    "Повышенный риск: confirmed paper execution требует ручного review/acknowledgement.",
  severe:
    "Жёсткий риск: confirmed paper execution блокируется safety overlay по умолчанию.",
};

const modeTone: Record<ExternalRiskRegimeMode, string> = {
  normal: "border-teal-200 bg-teal-50 text-teal-800",
  elevated: "border-amber-200 bg-amber-50 text-amber-800",
  severe: "border-red-200 bg-red-50 text-red-800",
};

function toDatetimeLocal(value: string | null | undefined): string {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  const local = new Date(parsed.getTime() - parsed.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function formFromRegime(regime: ExternalRiskRegime | null): RegimeFormState {
  return {
    mode: regime?.mode ?? "normal",
    reason: regime?.reason ?? "",
    source: regime?.source ?? "manual",
    expires_at: toDatetimeLocal(regime?.expires_at),
  };
}

function validateForm(form: RegimeFormState, confirmed: boolean): string[] {
  const errors: string[] = [];

  if (!form.mode) {
    errors.push("Нужно выбрать внешний режим.");
  }

  if ((form.mode === "elevated" || form.mode === "severe") && !form.reason.trim()) {
    errors.push("Для повышенного или жёсткого режима нужно указать причину.");
  }

  if ((form.mode === "elevated" || form.mode === "severe") && !confirmed) {
    errors.push("Подтвердите изменение внешнего режима.");
  }

  if (form.expires_at) {
    const expiresAt = new Date(form.expires_at).getTime();
    if (Number.isNaN(expiresAt)) {
      errors.push("Срок действия должен быть корректной датой и временем.");
    } else if (expiresAt <= Date.now()) {
      errors.push("Срок действия должен быть в будущем.");
    }
  }

  return errors;
}

function buildPayload(form: RegimeFormState): ExternalRiskRegimeUpdateRequest {
  return {
    mode: form.mode,
    reason: form.reason.trim() ? form.reason.trim() : null,
    source: form.source.trim() || "manual",
    expires_at: form.expires_at ? new Date(form.expires_at).toISOString() : null,
  };
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
      <span className="text-xs font-semibold uppercase text-slate-500">{label}</span>
      <span className="text-sm text-ink">{value || "—"}</span>
    </div>
  );
}

export function ExternalRiskRegimePage() {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<RegimeFormState>(() => formFromRegime(null));
  const [confirmed, setConfirmed] = useState(false);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const regimeQuery = useQuery({
    queryKey: ["external-risk-regime"],
    queryFn: api.getExternalRiskRegime,
  });

  const updateMutation = useMutation({
    mutationFn: (payload: ExternalRiskRegimeUpdateRequest) =>
      api.updateExternalRiskRegime(payload),
    onSuccess: (saved) => {
      setForm(formFromRegime(saved));
      setConfirmed(false);
      setValidationErrors([]);
      setSuccessMessage("Внешний режим обновлен.");
      queryClient.invalidateQueries({ queryKey: ["external-risk-regime"] });
      queryClient.invalidateQueries({ queryKey: ["live-paper", "overview"] });
    },
  });

  useEffect(() => {
    if (regimeQuery.data) {
      setForm(formFromRegime(regimeQuery.data));
      setConfirmed(false);
    }
  }, [regimeQuery.data]);

  const selectedDescription = useMemo(
    () => modeDescriptions[form.mode],
    [form.mode],
  );

  function updateField<K extends keyof RegimeFormState>(
    key: K,
    value: RegimeFormState[K],
  ) {
    setForm((current) => ({ ...current, [key]: value }));
    setSuccessMessage(null);
    if (key === "mode") {
      setConfirmed(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const errors = validateForm(form, confirmed);
    setValidationErrors(errors);
    setSuccessMessage(null);
    if (errors.length) {
      return;
    }
    updateMutation.mutate(buildPayload(form));
  }

  const currentRegime = regimeQuery.data;

  return (
    <div className="space-y-5">
      <section className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-sm font-semibold uppercase text-accent">
            External risk overlay
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-ink">
            Внешний режим пилота
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-slate-600">
            Операторский режим для macro, market-stress и иных внешних условий.
            Он не запускает paper execution и не меняет расписания.
          </p>
        </div>
        <Link className="text-button" to="/live-paper">
          К live paper dashboard
        </Link>
      </section>

      {regimeQuery.isLoading ? (
        <LoadingState label="Загрузка внешнего режима" />
      ) : null}

      {regimeQuery.isError ? (
        <ErrorState message={normalizeApiError(regimeQuery.error)} />
      ) : null}

      {currentRegime ? (
        <section
          className={`surface border p-4 ${modeTone[currentRegime.mode]}`}
        >
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div>
              <div className="inline-flex items-center gap-2 rounded-lg border border-current px-2 py-1 text-xs font-semibold uppercase">
                <ShieldAlert size={15} />
                {modeLabels[currentRegime.mode]}
              </div>
              <p className="mt-3 text-sm">{modeDescriptions[currentRegime.mode]}</p>
            </div>
            <div className="grid gap-2 text-sm md:min-w-72">
              <DetailRow label="Причина" value={currentRegime.reason} />
              <DetailRow label="Источник" value={currentRegime.source} />
              <DetailRow
                label="Действует до"
                value={formatDateTime(currentRegime.expires_at)}
              />
              <DetailRow
                label="Обновлено"
                value={formatDateTime(currentRegime.updated_at)}
              />
              <DetailRow
                label="Создано"
                value={formatDateTime(currentRegime.created_at)}
              />
            </div>
          </div>
        </section>
      ) : null}

      <form className="surface p-4" onSubmit={submit}>
        <div className="mb-4">
          <h2 className="text-base font-semibold text-ink">
            Обновить внешний режим
          </h2>
          <p className="mt-1 text-sm text-slate-600">{selectedDescription}</p>
        </div>

        <div className="grid gap-4 md:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block font-medium text-slate-700">Режим</span>
            <select
              className="w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink"
              onChange={(event) =>
                updateField("mode", event.target.value as ExternalRiskRegimeMode)
              }
              value={form.mode}
            >
              <option value="normal">{modeLabels.normal}</option>
              <option value="elevated">{modeLabels.elevated}</option>
              <option value="severe">{modeLabels.severe}</option>
            </select>
          </label>

          <label className="block text-sm">
            <span className="mb-1 block font-medium text-slate-700">Источник</span>
            <input
              className="w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink"
              onChange={(event) => updateField("source", event.target.value)}
              placeholder="manual"
              value={form.source}
            />
          </label>

          <label className="block text-sm md:col-span-2">
            <span className="mb-1 block font-medium text-slate-700">Причина</span>
            <textarea
              className="min-h-28 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink"
              onChange={(event) => updateField("reason", event.target.value)}
              placeholder="Краткое операторское пояснение"
              value={form.reason}
            />
          </label>

          <label className="block text-sm">
            <span className="mb-1 block font-medium text-slate-700">
              Действует до
            </span>
            <input
              className="w-full rounded-lg border border-line bg-white px-3 py-2 text-sm text-ink"
              onChange={(event) => updateField("expires_at", event.target.value)}
              type="datetime-local"
              value={form.expires_at}
            />
          </label>
        </div>

        {form.mode === "elevated" || form.mode === "severe" ? (
          <label className="mt-4 flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <input
              checked={confirmed}
              className="mt-1"
              onChange={(event) => setConfirmed(event.target.checked)}
              type="checkbox"
            />
            <span>
              Я подтверждаю изменение внешнего режима и понимаю, что confirmed
              paper execution потребует дополнительного ручного review или будет
              заблокирован safety overlay.
            </span>
          </label>
        ) : null}

        {validationErrors.length ? (
          <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <div className="flex items-center gap-2 font-semibold">
              <AlertTriangle size={16} />
              Проверьте форму
            </div>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {validationErrors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {updateMutation.isError ? (
          <div className="mt-4">
            <ErrorState message={normalizeApiError(updateMutation.error)} />
          </div>
        ) : null}

        {successMessage ? (
          <div className="mt-4 flex items-start gap-2 rounded-lg border border-teal-200 bg-teal-50 p-3 text-sm text-teal-800">
            <CheckCircle2 className="mt-0.5 shrink-0" size={17} />
            <span>{successMessage}</span>
          </div>
        ) : null}

        <div className="mt-4">
          <button
            className="primary-button"
            disabled={updateMutation.isPending}
            type="submit"
          >
            Сохранить внешний режим
          </button>
        </div>
      </form>
    </div>
  );
}
