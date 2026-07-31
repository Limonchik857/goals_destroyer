def section(request):
    app = request.resolver_match.app_name if request.resolver_match else ""
    url_name = request.resolver_match.url_name if request.resolver_match else ""
    if app in ("meetings", "votes", "agenda") or url_name == "team_home":
        section = "team"
    elif app == "focus":
        section = "focus"
    else:
        section = "tasks"
    return {"section": section, "team_section": section == "team"}
