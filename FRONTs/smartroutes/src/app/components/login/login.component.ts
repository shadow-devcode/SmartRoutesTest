import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink } from '@angular/router';
import { ReactiveFormsModule, FormBuilder, FormGroup, Validators } from '@angular/forms';
import { take } from 'rxjs/operators';
import { AuthService } from '../../services/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [CommonModule, ReactiveFormsModule, RouterLink],
  templateUrl: './login.component.html',
  styleUrl: './login.component.css',
})
export class LoginComponent implements OnInit {
  form: FormGroup;
  loading = false;
  errorMessage: string | null = null;
  showPassword = false;
  readonly currentYear = new Date().getFullYear();

  constructor(
    private fb: FormBuilder,
    private auth: AuthService,
    private router: Router
  ) {
    this.form = this.fb.group({
      email: ['', [Validators.required, Validators.email]],
      password: ['', [Validators.required, Validators.minLength(6)]],
    });
  }

  ngOnInit(): void {
    this.auth.sessionReady$.pipe(take(1)).subscribe(() => {
      if (this.auth.isAuthenticated()) {
        this.router.navigate([this.auth.isStaff() ? '/carga-excel' : '/rutas']);
      }
    });
  }

  onSubmit(): void {
    this.errorMessage = null;
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.loading = true;
    const { email, password } = this.form.getRawValue();
    this.auth.login(email, password).subscribe({
      next: () => {
        this.loading = false;
        this.router.navigate([this.auth.isStaff() ? '/carga-excel' : '/rutas']);
      },
      error: (err) => {
        this.loading = false;
        this.errorMessage = err?.error?.error ?? 'Error al iniciar sesión. Revisa tus credenciales.';
      },
    });
  }
}
