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
export interface Campaign { readonly [key: string]: unknown; }
export interface ICP { readonly [key: string]: unknown; }
export interface Account { readonly [key: string]: unknown; }
export interface ResearchJob {
  readonly job_id: string;
  readonly kind: string;
  readonly status: string;
  readonly campaign_id: string | null;
  readonly icp_id: string | null;
  readonly account_match_id: string | null;
  readonly started_at: string | null;
  readonly finished_at: string | null;
  readonly failure_detail: string | null;
  readonly attempt_count: number;
  readonly created_at: string;
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
    this.authenticated.set(false);
  }

  listDashboard(): Observable<any> { return this.authorized('GET', '/api/v1/dashboard'); }
  listWorkers(): Observable<any> { return this.authorized('GET', '/api/v1/workers'); }
  listCampaigns(): Observable<any> { return this.authorized('GET', '/api/v1/campaigns'); }
  listCampaignIcps(campaignId: string): Observable<any> {
    return this.authorized('GET', `/api/v1/campaigns/${campaignId}/icps`);
  }
  listAccounts(): Observable<any> { return this.authorized('GET', '/api/v1/accounts'); }
  listMemories(limit: number, offset: number, includeArchived: boolean): Observable<any> {
    return this.authorized('GET', '/api/v1/memories', {
      params: new HttpParams().set('limit', limit).set('offset', offset).set('include_archived', includeArchived),
    });
  }
  listJobs(): Observable<any> { return this.authorized('GET', '/api/v1/jobs'); }
  getJob(jobId: string): Observable<ResearchJob> {
    return this.authorized('GET', `/api/v1/jobs/${jobId}`);
  }
  listJobRuns(jobId: string): Observable<readonly AgentRun[]> {
    return this.authorized('GET', `/api/v1/jobs/${jobId}/agent-runs`);
  }
  listRunTurns(runId: string): Observable<readonly AgentTurn[]> {
    return this.authorized('GET', `/api/v1/agent-runs/${runId}/turns`);
  }
  listRunEvents(runId: string, afterEventId?: number, limit = 100): Observable<AgentTraceEventPage> {
    let params = new HttpParams().set('limit', limit);
    if (afterEventId !== undefined) params = params.set('after_event_id', afterEventId);
    return this.authorized('GET', `/api/v1/agent-runs/${runId}/events`, {params});
  }
  enqueueCampaignJobs(amount: number): Observable<any> {
    return this.authorized('POST', '/api/v1/jobs/campaigns', {body: {amount}});
  }
  enqueueIcpJob(campaignId: string): Observable<any> {
    return this.authorized('POST', '/api/v1/jobs', {body: {kind: 'icp', campaign_id: campaignId}});
  }
  enqueueAccountJob(campaignId: string, icpId: string): Observable<any> {
    return this.authorized('POST', '/api/v1/jobs', {body: {kind: 'account', campaign_id: campaignId, icp_id: icpId}});
  }
  cancelJob(jobId: string): Observable<any> {
    return this.authorized('POST', `/api/v1/jobs/${jobId}/cancel`, {body: {}});
  }
  deleteJob(jobId: string): Observable<any> { return this.authorized('DELETE', `/api/v1/jobs/${jobId}`); }

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
