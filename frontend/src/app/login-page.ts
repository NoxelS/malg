import {ChangeDetectionStrategy, Component, inject, signal} from '@angular/core';
import {FormsModule} from '@angular/forms';
import {ActivatedRoute, Router} from '@angular/router';
import {TuiButton, TuiRoot} from '@taiga-ui/core';

import {ApiService} from './api-service';

@Component({
  selector: 'app-login-page',
  imports: [FormsModule, TuiButton],
  template: `
    <section class="login" aria-labelledby="login-title">
      <h1 id="login-title">MALG</h1>
      <p>Sign in to the research workspace.</p>
      <form (ngSubmit)="submit()">
        <label>Username <input name="username" [(ngModel)]="username" required /></label>
        <label>Password <input name="password" type="password" [(ngModel)]="password" required /></label>
        @if (error()) { <p role="alert">Sign-in failed. Check your credentials or server configuration.</p> }
        <button tuiButton type="submit" [disabled]="submitting() || !password">{{ submitting() ? 'Signing in…' : 'Sign in' }}</button>
      </form>
    </section>
  `,
  styles: [`
    :host { display: block; min-height: 100dvh; }
    .login { box-sizing: border-box; display: grid; gap: 1rem; margin: 0 auto; max-width: 28rem; padding: 10rem 1.5rem; }
    h1, p { margin: 0; }
    form { display: grid; gap: 1rem; }
    label { display: grid; gap: .35rem; }
    input { border: 1px solid var(--tui-border-normal); border-radius: .5rem; padding: .7rem; }
  `],
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class LoginPage {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  protected username = 'admin';
  protected password = '';
  protected readonly submitting = signal(false);
  protected readonly error = signal(false);

  protected submit(): void {
    if (!this.password || this.submitting()) return;
    this.submitting.set(true);
    this.error.set(false);
    this.api.login(this.username, this.password).subscribe({
      next: () => {
        this.password = '';
        this.submitting.set(false);
        void this.router.navigateByUrl(this.safeReturnUrl());
      },
      error: () => {
        this.submitting.set(false);
        this.error.set(true);
      },
    });
  }

  private safeReturnUrl(): string {
    const value = this.route.snapshot.queryParamMap.get('returnUrl');
    return value?.startsWith('/') && !value.startsWith('//') ? value : '/dashboard';
  }
}
