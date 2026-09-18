"""Physical constants for the generation models.

Single source of truth: models/ddl.py interpolates these directly into the
generated SQL, so a coefficient has exactly one definition, never two.

Verified-vs-assumed status and citations: docs/research/2026-09-18-model-coefficients.md
"""

# -- PV --------------------------------------------------------------------

# Nominal operating cell temperature, degC. ASSUMED: typical crystalline-
# silicon value; real modules vary ~42-48 degC. No site-specific datasheet.
NOCT = 45.0

# Temperature coefficient of P_max, per degC. ASSUMED: a datasheet convention
# (commonly -0.3%/degC to -0.5%/degC), not a standards constant.
GAMMA = -0.004

# -- Wind --------------------------------------------------------------------

# Specific gas constant for dry air, J/(kg*K). VERIFIED: ICAO/ISO 2533
# Standard Atmosphere.
R_SPECIFIC = 287.058

# Standard air density at 101.325 kPa, 15 degC, kg/m^3. VERIFIED: ICAO/ISO 2533.
RHO_STANDARD = 1.2250

# IEA 3.4 MW / 130 RWT reference turbine. VERIFIED: NREL/TP-5000-73492.
RATED_KW = 3370.0
CUT_IN_MS = 3.0
CUT_OUT_MS = 25.0

# -- Unit conversions (exact, not physical assumptions) ----------------------

# Open-Meteo's wind_speed_100m arrives in km/h -- the km/h trap this slice
# guards against. NOT a coefficient to cite; a definitional conversion.
KMH_TO_MS = 3.6
HPA_TO_PA = 100.0
CELSIUS_TO_KELVIN = 273.15
