from django.core.paginator import Paginator

DEFAULT_PAGE_SIZE = 20


def paginate(request, object_list, *, page_param="page", per_page=DEFAULT_PAGE_SIZE):
    """统一分页并返回不含当前页码的查询参数，供模板保留筛选条件。"""
    page_obj = Paginator(object_list, per_page).get_page(request.GET.get(page_param))
    query = request.GET.copy()
    query.pop(page_param, None)
    return page_obj, query.urlencode()
