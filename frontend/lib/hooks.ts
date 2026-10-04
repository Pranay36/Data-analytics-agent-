"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import type { Analysis } from "@/types/api";

export const useDatasources = () =>
  useQuery({ queryKey: ["datasources"], queryFn: api.listDatasources });

export const useSchema = (id: string) =>
  useQuery({ queryKey: ["schema", id], queryFn: () => api.getSchema(id) });

export const useExamples = (id: string | undefined) =>
  useQuery({
    queryKey: ["examples", id],
    queryFn: () => api.getExamples(id as string),
    enabled: Boolean(id),
  });

export const useHistory = () =>
  useQuery({ queryKey: ["analyses"], queryFn: () => api.listAnalyses({ limit: 50 }) });

const isFinished = (a: Analysis | undefined) => a?.status === "completed" || a?.status === "failed";

/**
 * Polls once a second until the run finishes, then stops. A run lives in the database, so
 * this works the same on a fresh load, after a refresh, and from the history list.
 */
export function useAnalysis(id: string) {
  return useQuery({
    queryKey: ["analysis", id],
    queryFn: () => api.getAnalysis(id),
    refetchInterval: (query) => (isFinished(query.state.data) ? false : 1000),
    staleTime: 0,
  });
}

export function useStartAnalysis() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ datasourceId, question }: { datasourceId: string; question: string }) =>
      api.startAnalysis(datasourceId, question),
    onSuccess: () => client.invalidateQueries({ queryKey: ["analyses"] }),
  });
}

export function useInvalidateDatasources() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: ["datasources"] });
}
