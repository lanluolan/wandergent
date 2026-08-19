package com.wandergent.app.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * Brand palette: a deep teal that reads as "travel" without the airline-blue cliche,
 * with a warm amber accent used only for costs and highlights so money always looks
 * the same wherever it appears.
 *
 * Dynamic colour is deliberately not used -- the app should look the same on every
 * device when it goes into a portfolio screenshot.
 *
 * **Every neutral role is set explicitly, and that is not optional.** `lightColorScheme`
 * keeps Material's baseline (purple) values for whatever you leave out, and the roles
 * left out are the ones that paint the largest areas: `Card` draws on
 * `surfaceContainerLow`, `NavigationBar` on `surfaceContainer`. Setting only `surface`
 * and `primary` shipped a teal app with lavender cards and a pink-grey nav bar --
 * visible on device long before anyone thought to check the palette.
 */
private val Teal = Color(0xFF00696E)
private val TealLight = Color(0xFF6FF6FF)
private val TealDark = Color(0xFF4FD8E0)
private val Amber = Color(0xFF7A5900)
private val AmberLight = Color(0xFFFFDF9B)
private val Sand = Color(0xFFFAFDFC)
private val Ink = Color(0xFF191C1D)

private val LightColors = lightColorScheme(
    primary = Teal,
    onPrimary = Color.White,
    primaryContainer = TealLight,
    onPrimaryContainer = Color(0xFF002022),
    secondary = Color(0xFF4A6365),
    secondaryContainer = Color(0xFFCCE8E9),
    onSecondaryContainer = Color(0xFF051F21),
    tertiary = Amber,
    tertiaryContainer = AmberLight,
    onTertiaryContainer = Color(0xFF261A00),
    background = Sand,
    onBackground = Ink,
    surface = Sand,
    onSurface = Ink,
    surfaceVariant = Color(0xFFDAE4E5),
    onSurfaceVariant = Color(0xFF3F4849),
    // Neutrals, tinted towards the teal so cards and bars belong to the same palette.
    surfaceDim = Color(0xFFD9DFDE),
    surfaceBright = Sand,
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceContainerLow = Color(0xFFF3F7F6),
    surfaceContainer = Color(0xFFEDF2F1),
    surfaceContainerHigh = Color(0xFFE7ECEC),
    surfaceContainerHighest = Color(0xFFE1E7E6),
    outline = Color(0xFF6F7979),
    outlineVariant = Color(0xFFBEC8C9),
    inverseSurface = Color(0xFF2D3131),
    inverseOnSurface = Color(0xFFEFF1F1),
    inversePrimary = TealDark,
    surfaceTint = Teal,
    scrim = Color(0xFF000000),
    error = Color(0xFFBA1A1A),
    errorContainer = Color(0xFFFFDAD6),
    onErrorContainer = Color(0xFF410002),
)

private val DarkColors = darkColorScheme(
    primary = TealDark,
    onPrimary = Color(0xFF003739),
    primaryContainer = Color(0xFF004F53),
    onPrimaryContainer = TealLight,
    secondary = Color(0xFFB0CCCD),
    secondaryContainer = Color(0xFF324B4D),
    onSecondaryContainer = Color(0xFFCCE8E9),
    tertiary = Color(0xFFEBC248),
    tertiaryContainer = Color(0xFF5C4300),
    onTertiaryContainer = AmberLight,
    background = Color(0xFF0E1415),
    onBackground = Color(0xFFDEE4E4),
    surface = Color(0xFF0E1415),
    onSurface = Color(0xFFDEE4E4),
    surfaceVariant = Color(0xFF3F4849),
    onSurfaceVariant = Color(0xFFBEC8C9),
    surfaceDim = Color(0xFF0E1415),
    surfaceBright = Color(0xFF343A3A),
    surfaceContainerLowest = Color(0xFF090F10),
    surfaceContainerLow = Color(0xFF161D1D),
    surfaceContainer = Color(0xFF1A2121),
    surfaceContainerHigh = Color(0xFF252B2C),
    surfaceContainerHighest = Color(0xFF303636),
    outline = Color(0xFF899393),
    outlineVariant = Color(0xFF3F4849),
    inverseSurface = Color(0xFFDEE4E4),
    inverseOnSurface = Color(0xFF2B3231),
    inversePrimary = Teal,
    surfaceTint = TealDark,
    scrim = Color(0xFF000000),
    error = Color(0xFFFFB4AB),
    errorContainer = Color(0xFF93000A),
    onErrorContainer = Color(0xFFFFDAD6),
)

@Composable
fun WandergentTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(colorScheme = if (darkTheme) DarkColors else LightColors, content = content)
}
