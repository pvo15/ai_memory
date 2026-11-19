import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { apiFetch, qs } from './client';
import type {
    ActionName,
    BackupFile,
    CodeSearch,
    Explanation,
    FileHit,
    FileView,
    Health,
    JiraDetail,
    JiraRow,
    Job,
    LogLine,
    Memory,
    Overview,
    RuleFileView,
    RuleHit,
    RulesList,
    SyncStatus,
} from './types';

/** Auto-refresh interval for "live" data (ms); `false` when the user turned it off. */
export type Poll = number | false;

export const useOverview = (poll: Poll) =>
    useQuery({ queryKey: ['overview'], queryFn: () => apiFetch<Overview>('/api/overview'), refetchInterval: poll });

export const useJobs = (poll: Poll) =>
    useQuery({ queryKey: ['jobs'], queryFn: () => apiFetch<Record<string, Job>>('/api/jobs'), refetchInterval: poll });

export const useJira = (q: string, status: string) =>
    useQuery({
        queryKey: ['jira', q, status],
        queryFn: () => apiFetch<JiraRow[]>(`/api/jira?${qs({ q, status })}`),
        placeholderData: keepPreviousData,
    });

export const useJiraStory = (key: string | null) =>
    useQuery({
        queryKey: ['story', key],
        queryFn: () => apiFetch<JiraDetail>(`/api/jira/story?${qs({ key: key ?? '' })}`),
        enabled: !!key,
    });

export const useMemories = (q: string) =>
    useQuery({
        queryKey: ['memories', q],
        queryFn: () => apiFetch<Memory[]>(`/api/memories?${qs({ q })}`),
        placeholderData: keepPreviousData,
    });

export const useCodeSearch = (q: string, path: string) =>
    useQuery({
        queryKey: ['code', q, path],
        queryFn: () => apiFetch<CodeSearch>(`/api/code/search?${qs({ q, path })}`),
        enabled: q.trim().length > 0,
        staleTime: 60_000,
    });

export const useFindFiles = (q: string) =>
    useQuery({
        queryKey: ['files', q],
        queryFn: () => apiFetch<{ results: FileHit[]; total: number; ms: number }>(`/api/code/files?${qs({ q })}`),
        enabled: q.trim().length > 0,
        placeholderData: keepPreviousData,
        staleTime: 30_000,
    });

export const useFile = (path: string | null) =>
    useQuery({
        queryKey: ['file', path],
        queryFn: () => apiFetch<FileView>(`/api/code/file?${qs({ path: path ?? '' })}`),
        enabled: !!path,
    });

export const useBackups = () =>
    useQuery({
        queryKey: ['backups'],
        queryFn: () => apiFetch<{ files: BackupFile[]; dir: string }>('/api/backups'),
    });

export const useLogs = (source: string, level: string, q: string, poll: Poll) =>
    useQuery({
        queryKey: ['logs', source, level, q],
        queryFn: () => apiFetch<LogLine[]>(`/api/logs?${qs({ source, level, q, n: 800 })}`),
        refetchInterval: poll,
        placeholderData: keepPreviousData,
    });

/** Find pasted code in the repo + git blame + stories. */
export const useExplain = () =>
    useMutation({
        mutationFn: (code: string) =>
            apiFetch<Explanation>('/api/explain', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ code }),
            }),
    });

export const useHealth = (poll: Poll) =>
    useQuery({ queryKey: ['health'], queryFn: () => apiFetch<Health>('/api/health'), refetchInterval: poll ? 3000 : false });

/** Stop one running job (id) or all of them ('all'). */
export function useStopJob() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (id: number | 'all') =>
            apiFetch<{ ok: boolean; message: string }>(`/api/jobs/stop/${id}`, { method: 'POST' }),
        onSettled: () => {
            for (const key of ['health', 'jobs', 'overview']) void qc.invalidateQueries({ queryKey: [key] });
        },
    });
}

export const useSyncStatus = (poll: Poll) =>
    useQuery({ queryKey: ['sync'], queryFn: () => apiFetch<SyncStatus>('/api/sync'), refetchInterval: poll });

/** Mark one of my memories private (stays here) or shared (goes to the team). */
export function useSetMemoryPrivate() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: ({ id, private: priv }: { id: number; private: boolean }) =>
            apiFetch<{ ok: boolean; message: string }>('/api/memory/private', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ id, private: priv }),
            }),
        onSettled: () => {
            for (const key of ['memories', 'sync']) void qc.invalidateQueries({ queryKey: [key] });
        },
    });
}

/** Start a background job; refresh the views that show its effect. */
export function useAction() {
    const qc = useQueryClient();
    return useMutation({
        mutationFn: (name: ActionName) =>
            apiFetch<{ ok: boolean; message: string }>(`/api/action/${name}`, { method: 'POST' }),
        onSettled: () => {
            for (const key of ['jobs', 'overview', 'backups', 'health', 'sync', 'memories']) void qc.invalidateQueries({ queryKey: [key] });
        },
    });
}

export const useRules = (project: string) =>
    useQuery({
        queryKey: ['rules', project],
        queryFn: () => apiFetch<RulesList>(`/api/rules?${qs({ project })}`),
        enabled: !!project,
    });

export const useRuleFile = (project: string, file: string | null) =>
    useQuery({
        queryKey: ['rule-file', project, file],
        queryFn: () => apiFetch<RuleFileView>(`/api/rules/file?${qs({ project, file: file ?? '' })}`),
        enabled: !!project && !!file,
    });

export const useRuleSearch = (project: string, q: string) =>
    useQuery({
        queryKey: ['rule-search', project, q],
        queryFn: () => apiFetch<{ project: string; results: RuleHit[]; error?: string }>(`/api/rules/search?${qs({ project, q })}`),
        enabled: !!project && q.trim().length > 0,
        placeholderData: keepPreviousData,
    });
