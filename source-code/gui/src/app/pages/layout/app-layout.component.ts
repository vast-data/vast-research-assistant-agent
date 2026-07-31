import { Component, OnInit, inject, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterOutlet, ActivatedRoute, Router } from '@angular/router';
import { TopBarComponent } from './top-bar/top-bar.component';
import { SidebarComponent } from './sidebar/sidebar.component';
import { AuthService } from '../../features/auth/services/auth.service';
import { SessionsService } from '../../features/sessions/services/sessions.service';
import { CollectionsService } from '../../features/collections/services/collections.service';
import { AppStoreService } from '../../features/shared/services/app-store.service';

@Component({
  selector: 'app-app-layout',
  standalone: true,
  imports: [
    CommonModule,
    RouterOutlet,
    TopBarComponent,
    SidebarComponent,
  ],
  templateUrl: './app-layout.component.html',
  styleUrl: './app-layout.component.scss',
})
export class AppLayoutComponent implements OnInit {
  route = inject(ActivatedRoute);
  router = inject(Router);
  authService = inject(AuthService);
  sessionsService = inject(SessionsService);
  collectionsService = inject(CollectionsService);
  private appStoreService = inject(AppStoreService);

  sidebarOpen = computed(() => this.appStoreService.sidebarOpen());

  closeSidebar(): void {
    this.appStoreService.closeSidebar();
  }

  constructor() {
    // CollectionsService / SessionsService already load once via their own effects
    // when auth becomes success. Do not re-trigger here (was causing 3–4x IE calls).
  }

  ngOnInit() {
    // Force-inject services so their constructors/effects run once.
    void this.sessionsService;
    void this.collectionsService;

    // Check for Azure AD session_id in query params (from redirect)
    this.route.queryParams.subscribe(params => {
      const sessionId = params['session_id'];
      const provider = params['provider'];

      if (sessionId && provider === 'azuread') {
        // Exchange session_id for token; services' auth effects load data once.
        this.authService.exchangeSessionToken(sessionId).subscribe({
          next: () => {
            setTimeout(() => {
              this.router.navigate([], {
                relativeTo: this.route,
                queryParams: {},
                replaceUrl: true
              });
            }, 200);
          },
          error: (err) => {
            console.error('Error in AppLayoutComponent session exchange:', err);
            this.router.navigate(['/login'], { queryParams: { error: 'session_exchange_failed' } });
          }
        });
      }
    });
  }
}