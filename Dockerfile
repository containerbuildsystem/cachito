FROM fedora:29
LABEL maintainer="Factory 2.0"

WORKDIR /src
RUN dnf -y install \
    --setopt=deltarpm=0 \
    --setopt=install_weak_deps=false \
    --setopt=tsflags=nodocs \
    httpd \
    mod_ssl \
    python3-celery \
    python3-flask \
    python3-flask-migrate \
    python3-flask-sqlalchemy \
    python3-psycopg2 \
    python3-mod_wsgi \
    && dnf clean all
COPY . .
COPY ./docker/cachito-httpd.conf /etc/httpd/conf/httpd.conf
RUN pip3 install . --no-deps
EXPOSE 8080
CMD ["/usr/sbin/httpd", "-DFOREGROUND"]
