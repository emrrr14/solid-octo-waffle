# Macro factor model in R - the validation twin of app/analytics/regression.py.
#
# Keep both: R is where the statistics get argued about (NeweyWest, Type-II
# ANOVA, diagnostics are one line each), Python is where the model runs in
# production.  Any beta that differs between the two by more than rounding is a
# bug in one of them - that is the point of having the pair.
#
# install.packages(c("sandwich", "lmtest", "car", "lpSolve"))

suppressPackageStartupMessages({
  library(sandwich)   # HAC covariance
  library(lmtest)     # coeftest
  library(car)        # Anova (Type II)
  library(lpSolve)    # the same MAD LP, solved by simplex
})

# --- 1. Estimation ----------------------------------------------------------

fit_factor_model <- function(returns, factors, alpha_level = 0.10) {
  # returns: numeric vector; factors: data.frame with the same number of rows.
  df  <- na.omit(cbind(y = returns, factors))
  fit <- lm(y ~ ., data = df)

  lags <- max(1, floor(4 * (nrow(df) / 100)^(2 / 9)))          # Newey-West rule of thumb
  hac  <- NeweyWest(fit, lag = lags, prewhite = FALSE)
  tt   <- coeftest(fit, vcov. = hac)

  betas <- coef(fit)[-1]
  pvals <- tt[-1, 4]
  betas[pvals > alpha_level] <- 0                              # shrinkage, as in Python

  list(
    alpha     = unname(coef(fit)[1]),
    betas     = betas,
    pvalues   = pvals,
    r_squared = summary(fit)$r.squared,
    resid_vol = sd(residuals(fit)),
    n_obs     = nrow(df),
    anova     = car::Anova(fit, type = 2),                     # does the block explain variance?
    hac_test  = tt
  )
}

expected_return <- function(model, scenario) {
  mu <- model$alpha
  for (f in names(model$betas)) {
    if (!is.null(scenario[[f]])) mu <- mu + model$betas[[f]] * scenario[[f]]
  }
  unname(mu)
}

# --- 2. Allocation LP (Konno-Yamazaki MAD), solved with simplex --------------

solve_mad_lp <- function(mu, paths, min_return, upper = NULL) {
  # paths: T x N scenario-conditioned returns.  Variables: [w_1..w_N, y_1..y_T].
  T <- nrow(paths); N <- ncol(paths)
  D <- scale(paths, center = TRUE, scale = FALSE)

  obj <- c(rep(0, N), rep(1 / T, T))

  mad_pos <- cbind( D, -diag(T))          #  D w - y <= 0
  mad_neg <- cbind(-D, -diag(T))          # -D w - y <= 0
  ret     <- c(-mu, rep(0, T))            # -mu' w   <= -min_return
  budget  <- c(rep(1, N), rep(0, T))      #  sum w    = 1

  con <- rbind(mad_pos, mad_neg, ret, budget)
  dir <- c(rep("<=", 2 * T), "<=", "=")
  rhs <- c(rep(0, 2 * T), -min_return, 1)

  if (!is.null(upper)) {                  # per-instrument caps
    caps <- cbind(diag(N), matrix(0, N, T))
    con  <- rbind(con, caps); dir <- c(dir, rep("<=", N)); rhs <- c(rhs, upper)
  }

  sol <- lp("min", obj, con, dir, rhs)
  list(status = sol$status, weights = sol$solution[1:N], mad = sol$objval)
}

# --- 3. Demo ----------------------------------------------------------------

if (sys.nframe() == 0) {
  set.seed(7)
  n <- 750
  surprise <- rep(0, n); surprise[sample(n, n %/% 40)] <- sample(c(-25, 25), n %/% 40, TRUE)
  factors  <- data.frame(fed_surprise_bps = surprise,
                         d_log_usdtry     = 0.0004 * surprise + rnorm(n, 0, 0.006))

  funds <- list(GLD = -0.00030, EQ = -0.00022, BOND = -0.00008, MM = 0.00001)
  paths <- sapply(names(funds), function(k)
    0.0005 + funds[[k]] * factors$fed_surprise_bps + rnorm(n, 0, 0.01))

  models <- lapply(names(funds), function(k) fit_factor_model(paths[, k], factors))
  names(models) <- names(funds)
  print(models$GLD$anova)

  scenario <- list(fed_surprise_bps = -25, d_log_usdtry = -0.01)     # 25bp dovish surprise
  mu  <- sapply(models, expected_return, scenario = scenario)
  sol <- solve_mad_lp(mu, paths, min_return = mean(mu), upper = rep(0.35, length(mu)))

  cat("\nweights:\n"); print(round(setNames(sol$weights, names(funds)), 4))
  cat("MAD:", sol$mad, " status:", sol$status, "\n")
  cat("500 TL:\n"); print(round(500 * sol$weights, 2))
}
