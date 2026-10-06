import { inject, Injectable } from '@angular/core';
import { Title } from '@angular/platform-browser';
import { TitleStrategy } from '@angular/router';
import type { RouterStateSnapshot } from '@angular/router';

/** Applies MALG branding to the page name resolved by Angular navigation. */
@Injectable()
export class PageTitleStrategy extends TitleStrategy {
  private readonly title = inject(Title);

  /** Updates the document title after navigation, including routes without a title. */
  override updateTitle(snapshot: RouterStateSnapshot): void {
    const pageTitle = this.buildTitle(snapshot);
    this.title.setTitle(pageTitle ? `MALG ${pageTitle} - Multi Agent Lead Generation` : 'MALG - Multi Agent Lead Generation');
  }
}
