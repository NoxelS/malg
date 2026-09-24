import {HttpClient, HttpErrorResponse, HttpParams} from '@angular/common/http';
import {inject, Injectable, signal} from '@angular/core';
import {Router} from '@angular/router';
import {Observable, throwError} from 'rxjs';
import {catchError, tap} from 'rxjs/operators';

export interface TokenResponse {
  readonly access_token: string;
  readonly token_type: 'bearer';
  readonly expires_in: number;
}

export interface DashboardSummary { readonly [key: string]: unknown; }
export interface WorkerSummary { readonly [key: string]: unknown; }
export interface CrmListItem {
  readonly id: string;
  readonly display_name: string;
  readonly url: string | null;
}
export interface CrmDetails extends CrmListItem {
  readonly data: JsonValue;
  readonly campaign_id: string | null;
  readonly company_id: string | null;
  readonly updated_at: string;
}
export interface CrmPage<T extends CrmListItem = CrmListItem> {
  readonly items: readonly T[];
  readonly next_cursor: string | null;
}
export interface CrmStatus {
  readonly available: boolean;
  readonly schema_compatible: boolean;
  readonly expected_contract_version: number;
  readonly expected_contract_hash: string;
  readonly reason?: string | null;
  readonly public_url?: string;
}
export type JobKind = 'campaign' | 'icp' | 'discovery' | 'account' | 'account_hydration' | 'person' | 'person_hydration';
export type ResearchOutcome = 'complete' | 'partial' | 'needs_review' | 'insufficient_evidence' | 'budget_exhausted';
export type ResearchJobRequest =
  | {kind: 'campaign'}
  | {kind: 'icp'; campaign_id: string}
  | {kind: 'discovery'; icp_id: string; campaign_id?: string; limit?: number}
  | {kind: 'account'; icp_id: string; campaign_id?: string; name?: string; website?: string}
  | {kind: 'account_hydration'; account_id: string}
  | {kind: 'person'; account_id: string; icp_id: string}
  | {kind: 'person_hydration'; person_id: string};
export interface ResultReference { readonly object_name: string; readonly record_id: string; readonly url: string | null; }
export interface ResearchJob {
  readonly job_id: string;
  readonly kind: JobKind | string;
  readonly status: string;
  readonly campaign_id: string | null;
  readonly icp_id: string | null;
  readonly account_id: string | null;
  readonly person_id: string | null;
  readonly data_origin?: string;
  readonly result_outcome?: ResearchOutcome | null;
  readonly result_refs: readonly ResultReference[];
  readonly request_payload?: JsonValue | null;
  readonly contract_version?: number | null;
  readonly contract_hash?: string | null;
  readonly workflow_id: string | null;
  readonly stage_key: string | null;
  readonly deadline_at: string | null;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly failure_detail: string | null;
  readonly attempt_count: number;
  readonly created_at: string;
}
export interface StageRecord {
  readonly stage_result_id: string;
  readonly stage_key: string;
  readonly outcome: string;
  readonly revision: number;
  readonly unknowns: readonly string[];
  readonly source_refs: readonly string[];
  readonly created_at: string;
  readonly payload: JsonValue;
}
export interface StagePage {
  readonly workflow_id: string | null;
  readonly input_payload: JsonValue;
  readonly items: readonly StageRecord[];
}
export interface CrmWriteOperation {
  readonly operation_id: string;
  readonly stage_key: string;
  readonly object_name: string;
  readonly record_id: string;
  readonly field_key: string | null;
  readonly status: string;
  readonly attempt_count: number;
  readonly intended_fields: JsonValue;
  readonly observed_record_version: string | null;
  readonly sanitized_error: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}
export interface JobWrites {
  readonly items: readonly CrmWriteOperation[];
  readonly side_effects_pending: boolean;
}
export interface Paginated<T> {
  readonly items: readonly T[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
}
export type JsonValue = null | boolean | number | string | JsonValue[] | {[key: string]: JsonValue};
export interface AgentRun {
  readonly run_id: string;
  readonly job_id: string;
  readonly scope: 'worker' | 'agent';
  readonly worker_token: string | null;
  readonly agent_name: string;
  readonly method_name: string;
  readonly status: 'running' | 'succeeded' | 'failed';
  readonly started_at: string;
  readonly finished_at: string | null;
  readonly error_type: string | null;
  readonly error_message: string | null;
  readonly error_traceback: string | null;
}
export interface AgentTurn {
  readonly turn_id: number;
  readonly run_id: string;
  readonly generation_id: string;
  readonly turn_number: number;
  readonly method_name: string;
  readonly strategy: string;
  readonly started_at: string;
  readonly finished_at: string | null;
  readonly success: boolean | null;
  readonly error_type: string | null;
  readonly error_message: string | null;
  readonly error_traceback: string | null;
  readonly request_messages: readonly JsonValue[];
  readonly request_params: JsonValue;
  readonly response: JsonValue;
}
export interface AgentTraceEvent {
  readonly event_id: number;
  readonly run_id: string;
  readonly turn_id: number | null;
  readonly sequence: number;
  readonly occurred_at: string;
  readonly event_type: string;
  readonly payload: JsonValue;
}
export interface AgentTraceEventPage {
  readonly items: readonly AgentTraceEvent[];
  readonly next_after_event_id: number | null;
  readonly limit: number;
}

@Injectable({providedIn: 'root'})
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly tokenKey = 'malg.access_token';
  private readonly expiryKey = 'malg.access_token_expiry';
  readonly authenticated = signal(this.hasValidSession());

  login(username: string, password: string): Observable<TokenResponse> {
    return this.http.post<TokenResponse>('/api/v1/auth/token', {username, password}).pipe(
      tap((response) => {
        if (response.token_type !== 'bearer' || !response.expires_in || !response.access_token) {
          throw new Error('Invalid authentication response');
        }
        const expiry = Date.now() + response.expires_in * 1000;
        sessionStorage.setItem(this.tokenKey, response.access_token);
        sessionStorage.setItem(this.expiryKey, String(expiry));
        this.authenticated.set(true);
      }),
    );
  }

  logout(): void {
    sessionStorage.removeItem(this.tokenKey);
    sessionStorage.removeItem(this.expiryKey);
  }
  listDashboard(): Observable<any> { return this.authorized('GET', '/api/v1/dashboard'); }
  listWorkers(): Observable<any> { return this.authorized('GET', '/api/v1/workers'); }
  getCrmStatus(): Observable<CrmStatus> { return this.authorized('GET', '/api/v1/crm/status'); }
  listCrmCampaigns(cursor?: string): Observable<CrmPage> {
    return this.authorized('GET', '/api/v1/crm/campaigns', {params: cursor ? new HttpParams().set('cursor', cursor) : undefined});
  }
  listCrmIcps(campaignId?: string, cursor?: string): Observable<CrmPage> {
    let params = new HttpParams();
    if (campaignId) params = params.set('campaignId', campaignId);
    if (cursor) params = params.set('cursor', cursor);
    return this.authorized('GET', '/api/v1/crm/icps', {params});
  }
  listCrmAccounts(query = '', cursor?: string): Observable<CrmPage> {
    let params = new HttpParams().set('query', query);
    if (cursor) params = params.set('cursor', cursor);
    return this.authorized('GET', '/api/v1/crm/accounts', {params});
  }
  listCrmPeople(accountId: string, cursor?: string): Observable<CrmPage> {
    let params = new HttpParams().set('companyId', accountId);
    if (cursor) params = params.set('cursor', cursor);
    return this.authorized('GET', '/api/v1/crm/people', {params});
  }
  getCrmCampaign(id: string): Observable<CrmDetails> { return this.authorized('GET', `/api/v1/crm/campaigns/${id}`); }
  getCrmIcp(id: string): Observable<CrmDetails> { return this.authorized('GET', `/api/v1/crm/icps/${id}`); }
  getCrmAccount(id: string): Observable<CrmDetails> { return this.authorized('GET', `/api/v1/crm/accounts/${id}`); }
  getCrmPerson(id: string): Observable<CrmDetails> { return this.authorized('GET', `/api/v1/crm/people/${id}`); }
  listMemories(limit: number, offset: number, includeArchived: boolean): Observable<any> {
    return this.authorized('GET', '/api/v1/memories', {
      params: new HttpParams().set('limit', limit).set('offset', offset).set('include_archived', includeArchived),
    });
  }
  listJobs(): Observable<readonly ResearchJob[]> { return this.authorized('GET', '/api/v1/jobs'); }
  getJob(jobId: string): Observable<ResearchJob> { return this.authorized('GET', `/api/v1/jobs/${jobId}`); }
  listJobRuns(jobId: string): Observable<readonly AgentRun[]> { return this.authorized('GET', `/api/v1/jobs/${jobId}/agent-runs`); }
  listRunTurns(runId: string): Observable<readonly AgentTurn[]> { return this.authorized('GET', `/api/v1/agent-runs/${runId}/turns`); }
  listRunEvents(runId: string, afterEventId?: number, limit = 100): Observable<AgentTraceEventPage> {
    let params = new HttpParams().set('limit', limit);
    if (afterEventId !== undefined) params = params.set('after_event_id', afterEventId);
    return this.authorized('GET', `/api/v1/agent-runs/${runId}/events`, {params});
  }
  enqueueCampaignJobs(amount: number): Observable<readonly ResearchJob[]> {
    return this.authorized('POST', '/api/v1/jobs/campaigns', {body: {amount}});
  }
  enqueueIcpJobs(campaignId: string, amount = 1): Observable<readonly ResearchJob[]> {
    return this.authorized('POST', '/api/v1/jobs/icps', {body: {campaign_id: campaignId, amount}});
  }
  submitJob(body: ResearchJobRequest): Observable<ResearchJob> {
    return this.authorized('POST', '/api/v1/jobs', {body});
  }
  enqueueAccountJobs(icpId: string, amount = 1, campaignId?: string): Observable<readonly ResearchJob[]> {
    return this.authorized('POST', '/api/v1/jobs/accounts', {body: {icp_id: icpId, amount, ...(campaignId ? {campaign_id: campaignId} : {})}});
  }
  cancelJob(jobId: string): Observable<ResearchJob> { return this.authorized('POST', `/api/v1/jobs/${jobId}/cancel`, {body: {}}); }
  deleteJob(jobId: string): Observable<void> { return this.authorized('DELETE', `/api/v1/jobs/${jobId}`); }
  listJobStages(jobId: string): Observable<StagePage> {
    return this.authorized('GET', `/api/v1/jobs/${jobId}/stages`);
  }
  listJobWrites(jobId: string): Observable<JobWrites> { return this.authorized('GET', `/api/v1/jobs/${jobId}/writes`); }
  retryJob(jobId: string): Observable<ResearchJob> { return this.authorized('POST', `/api/v1/jobs/${jobId}/retry`, {body: {}}); }
  private authorized<T>(method: string, url: string, options: Record<string, unknown> = {}): Observable<T> {
    const token = this.validToken();
    if (!token) {
      this.expireSession();
      return throwError(() => new Error('Authentication required'));
    }
    return this.http.request<T>(method, url, {
      ...options,
      headers: {Authorization: `Bearer ${token}`},
    }).pipe(catchError((error: HttpErrorResponse) => {
      if (error.status === 401) this.expireSession(url);
      return throwError(() => error);
    }));
  }

  private validToken(): string | null {
    const token = sessionStorage.getItem(this.tokenKey);
    const expiry = Number(sessionStorage.getItem(this.expiryKey));
    return token && Number.isFinite(expiry) && expiry > Date.now() ? token : null;
  }

  private hasValidSession(): boolean { return this.validToken() !== null; }

  private expireSession(returnUrl = window.location.pathname + window.location.search): void {
    this.logout();
    void this.router.navigate(['/login'], {queryParams: {returnUrl}});
  }
}
